"""Committed query vectors: the only way a retrieval eval run gets a query embedding.

Every dense arm needs a vector for every query. A hosted embedding call per query per CI run
would break the cost ceiling and the claim that retrieval eval is free, so the vectors are
computed once, by ``--refresh``, and committed: one Parquet file per embedding configuration,
``evals/query_vectors/<model_id>_<dim>_<quantisation>.parquet``, mapping the sha256 of the
query text to its unit vector. 1,000 queries at 256 dimensions in float16 is half a megabyte.

During an eval run the embedder is ``CachedQueryEmbedder``. It reads that file and nothing
else: a query with no committed vector raises ``MissingQueryVectorError`` naming the query,
never a live call. ``--refresh`` is the one command allowed to call an embedding API, and it is
never run by an eval.

    uv run python -m evals.query_vectors --refresh        # the configured model, every query
    uv run python -m evals.query_vectors --refresh --scope sample \
        --model openai/text-embedding-3-large --dim 1024 --quantisation float32
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from extant.index.chunking import estimate_tokens
from extant.index.embed import Embedded, Embedder, Kind, ModelIdentity, TokenRate, l2_normalise

CACHE_DIR = Path(__file__).resolve().parent / "query_vectors"
QUANTISATIONS = {"float16": "e", "float32": "f"}  # struct format characters, little endian
REFRESH_BATCH = 256


class MissingQueryVectorError(LookupError):
    """A query has no committed vector. An eval run stops here rather than embed live."""


def query_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_path(identity: ModelIdentity, directory: Path = CACHE_DIR) -> Path:
    model = identity.model_id.replace("/", "-")
    return directory / f"{model}_{identity.dim}_{identity.quantisation}.parquet"


def _format(identity: ModelIdentity) -> str:
    try:
        return "<" + str(identity.dim) + QUANTISATIONS[identity.quantisation]
    except KeyError:
        raise ValueError(f"no query vector storage for {identity.quantisation!r}") from None


def _pack(identity: ModelIdentity, vector: Sequence[float]) -> bytes:
    if len(vector) != identity.dim:
        raise ValueError(f"vector has {len(vector)} dimensions, expected {identity.dim}")
    return struct.pack(_format(identity), *vector)


def _unpack(identity: ModelIdentity, blob: bytes) -> list[float]:
    return list(struct.unpack(_format(identity), blob))


def read_vectors(path: Path, identity: ModelIdentity) -> dict[str, list[float]]:
    """The committed file as ``{sha256: vector}``; checks it belongs to ``identity``."""
    table = pq.read_table(path)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    expected = {
        "model_id": identity.model_id,
        "dim": str(identity.dim),
        "quantisation": identity.quantisation,
    }
    found = {k: meta.get(k) for k in expected}
    if found != expected:
        raise ValueError(f"{path.name} holds vectors for {found}, not {expected}")
    hashes = table.column("sha256").to_pylist()
    blobs = table.column("vector").to_pylist()
    return {h: _unpack(identity, b) for h, b in zip(hashes, blobs, strict=True)}


def write_vectors(
    path: Path, identity: ModelIdentity, vectors: Mapping[str, Sequence[float]]
) -> None:
    """Rows sorted by hash with fixed writer settings, so the same vectors give the same
    bytes and a refresh that changed nothing shows no diff."""
    hashes = sorted(vectors)
    schema = pa.schema(
        [("sha256", pa.string()), ("vector", pa.binary())],
        metadata={
            "model_id": identity.model_id,
            "dim": str(identity.dim),
            "quantisation": identity.quantisation,
        },
    )
    table = pa.table(
        {"sha256": hashes, "vector": [_pack(identity, vectors[h]) for h in hashes]}, schema=schema
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd", use_dictionary=False, write_statistics=False)
    tmp.replace(path)


class CachedQueryEmbedder:
    """The ``Embedder`` an eval run uses: committed vectors only, and no network at all."""

    def __init__(self, identity: ModelIdentity, directory: Path = CACHE_DIR) -> None:
        self.identity = identity
        self.path = cache_path(identity, directory)
        if not self.path.exists():
            raise MissingQueryVectorError(
                f"no committed query vectors for {identity.model_id} @ {identity.dim}"
                f" {identity.quantisation}: {self.path} does not exist; run"
                " `uv run python -m evals.query_vectors --refresh` for this configuration"
            )
        self._vectors = read_vectors(self.path, identity)

    def __len__(self) -> int:
        return len(self._vectors)

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        if kind != "query":
            raise ValueError("the query vector cache holds queries only")
        out: list[list[float]] = []
        for text in texts:
            vector = self._vectors.get(query_hash(text))
            if vector is None:
                raise MissingQueryVectorError(
                    f"no committed vector for the query {text!r} in {self.path.name};"
                    " the eval sets changed since the last refresh: run"
                    " `uv run python -m evals.query_vectors --refresh` and commit the file"
                )
            out.append(vector)
        return Embedded(out, 0)


@dataclass(frozen=True)
class RefreshReport:
    path: Path
    kept: int
    embedded: int
    dropped: int
    tokens: int

    def __str__(self) -> str:
        return (
            f"{self.path.name}: {self.kept + self.embedded:,} queries; {self.kept:,} kept,"
            f" {self.embedded:,} embedded ({self.tokens:,} tokens), {self.dropped:,} dropped"
        )


def refresh(
    texts: Iterable[str],
    embedder: Embedder,
    *,
    directory: Path = CACHE_DIR,
    force: bool = False,
    tokens_per_minute: int | None = None,
    rate: TokenRate | None = None,
) -> RefreshReport:
    """Make the file hold exactly the vectors for ``texts``. Vectors already committed for
    an unchanged text are kept unless ``force``: the provider's vectors drift slightly from
    call to call, and a refresh that rewrote every row would churn every eval number."""
    identity = embedder.identity
    path = cache_path(identity, directory)
    wanted = {query_hash(t): t for t in texts}
    existing = read_vectors(path, identity) if path.exists() and not force else {}
    keep = {h: v for h, v in existing.items() if h in wanted}
    missing = sorted((h, t) for h, t in wanted.items() if h not in keep)
    pace = rate or (TokenRate(tokens_per_minute) if tokens_per_minute else None)
    tokens = 0
    for start in range(0, len(missing), REFRESH_BATCH):
        batch = missing[start : start + REFRESH_BATCH]
        entry = pace.reserve(sum(estimate_tokens(t) for _, t in batch)) if pace else None
        result = embedder.embed([t for _, t in batch], kind="query")
        if entry is not None:
            TokenRate.settle(entry, result.tokens)
        tokens += result.tokens
        for (h, _), vector in zip(batch, result.vectors, strict=True):
            keep[h] = l2_normalise(vector)
    write_vectors(path, identity, keep)
    dropped = sum(1 for h in existing if h not in wanted)
    return RefreshReport(path, len(wanted) - len(missing), len(missing), dropped, tokens)


# --------------------------------------------------------------------------- command line


def _embedder(args: argparse.Namespace) -> Embedder:
    from extant.index.settings import load_index_settings

    settings = load_index_settings()
    model = args.model or settings.identity.model_id
    dim = args.dim or settings.embedding_dim
    if model.startswith("openai/"):
        from pydantic import SecretStr

        from extant.index.embed import OpenAIEmbedder

        key: SecretStr | None = settings.openai_api_key
        if key is None:
            raise SystemExit("error: EXTANT_OPENAI_API_KEY is not set")
        embedder: Embedder = OpenAIEmbedder(
            key.get_secret_value(), model.removeprefix("openai/"), dim=dim
        )
    elif model.startswith("local/"):
        from evals.ablation.models import LocalEmbedder

        embedder = LocalEmbedder(model.removeprefix("local/"), dim=dim)
    else:
        raise SystemExit(f"error: no refresh path for {model}")
    # The storage precision of the committed file; the provider returns full floats either way.
    embedder.identity = replace(embedder.identity, quantisation=args.quantisation)
    return embedder


def main(argv: Sequence[str] | None = None) -> int:
    from evals.sets import load_sets, sample_queries

    parser = argparse.ArgumentParser(prog="python -m evals.query_vectors")
    parser.add_argument("--refresh", action="store_true", required=True)
    parser.add_argument(
        "--model", help="model id, e.g. openai/text-embedding-3-large or local/bge-m3"
    )
    parser.add_argument("--dim", type=int)
    parser.add_argument("--quantisation", choices=sorted(QUANTISATIONS), default="float16")
    parser.add_argument(
        "--scope",
        choices=["all", "sample"],
        default="all",
        help="every eval query, or only those of the reduced-corpus sample (arms 5 and 6)",
    )
    parser.add_argument("--force", action="store_true", help="re-embed every query")
    args = parser.parse_args(argv)
    embedder = _embedder(args)
    queries = (
        sample_queries()
        if args.scope == "sample"
        else [q for s in load_sets().values() for q in s.queries]
    )
    texts = sorted({q.text for q in queries})
    from extant.index.settings import load_index_settings

    hosted = embedder.identity.model_id.startswith("openai/")
    tpm = load_index_settings().embed_tokens_per_minute if hosted else None
    report = refresh(texts, embedder, force=args.force, tokens_per_minute=tpm)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
