"""The reduced-corpus build for arms 4 to 6.

    uv run python -m evals.sets sample                      # draw it, once
    uv run python -m evals.ablation.sample_index embed \
        --model openai/text-embedding-3-large --dim 1024 --quantisation float32 --dry-run
    uv run --group ablation python -m evals.ablation.sample_index embed \
        --model local/bge-m3 --dim 1024
    uv run python -m evals.ablation.sample_index build

The sample's documents (every gold document of the evaluated queries plus seeded distractors)
are built into their own schema, ``idx_rbisample_<date>_<commit>``, through the ordinary
assemble path with the serving vectors from ``public.vec_cache``. Two columns are then added
beside ``embedding``: ``emb_oai1024 vector(1024)`` and ``emb_bgem3 halfvec(1024)``, filled from
vector files under ``data/evals/ablation/`` (gitignored: document vectors are derived data).
The serving schema never has these columns; only this build does.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import date

import psycopg
from psycopg import sql

from evals.arms import HOSTED_256, SAMPLE_COLUMNS
from evals.query_vectors import cache_path, read_vectors, write_vectors
from evals.sets import DATA_DIR, read_sample
from extant.core.identity import make_schema_name
from extant.corpora.rbi.build import git_state, load_inputs
from extant.corpora.rbi.chunk import chunk_all, load_chunks
from extant.index.assemble import BuildInputs, assemble
from extant.index.chunking import estimate_tokens
from extant.index.embed import (
    PRICE_USD_PER_MTOK,
    Embedded,
    Embedder,
    Kind,
    ModelIdentity,
    TokenRate,
    batch_budget,
    halfvec_literal,
    l2_normalise,
)

VECTORS_DIR = DATA_DIR.parent / "evals" / "ablation"
SAVE_EVERY = 10  # batches between writes, so an interrupted run keeps what it paid for


def sample_texts() -> dict[str, str]:
    """``{content_hash: embed_text}`` for every chunk of the sample's documents."""
    docs = set(_sample_docs())
    return {c.content_hash: c.embed_text for c in load_chunks(DATA_DIR) if c.doc_id in docs}


def _sample_docs() -> list[str]:
    sample = read_sample()
    return sorted({*sample["gold_documents"], *sample["distractor_documents"]})


def embed_sample(
    embedder: Embedder, *, tokens_per_minute: int | None, dry_run: bool = False
) -> str:
    texts = sample_texts()
    path = cache_path(embedder.identity, VECTORS_DIR)
    have = read_vectors(path, embedder.identity) if path.exists() else {}
    todo = sorted((h, t) for h, t in texts.items() if h not in have)
    tokens = sum(estimate_tokens(t) for _, t in todo)
    price = PRICE_USD_PER_MTOK.get(embedder.identity.model_id, 0.0)
    plan = (
        f"{embedder.identity.model_id} @ {embedder.identity.dim} {embedder.identity.quantisation}:"
        f" {len(texts):,} sample texts, {len(todo):,} to embed, about {tokens:,} tokens"
        f" (characters / 4), about ${tokens / 1e6 * price:.3f}"
    )
    if dry_run or not todo:
        return plan
    budget = batch_budget(tokens_per_minute)
    rate = TokenRate(tokens_per_minute) if tokens_per_minute else None
    batches: list[list[tuple[str, str]]] = [[]]
    for item in todo:
        if (
            batches[-1]
            and sum(estimate_tokens(t) for _, t in batches[-1]) + estimate_tokens(item[1]) > budget
        ):
            batches.append([])
        batches[-1].append(item)
    spent = 0
    for i, batch in enumerate(batches, start=1):
        entry = rate.reserve(sum(estimate_tokens(t) for _, t in batch)) if rate else None
        result = embedder.embed([t for _, t in batch], kind="document")
        if entry is not None:
            TokenRate.settle(entry, result.tokens)
        spent += result.tokens
        for (h, _), vector in zip(batch, result.vectors, strict=True):
            have[h] = l2_normalise(vector)
        if i % SAVE_EVERY == 0 or i == len(batches):
            write_vectors(path, embedder.identity, have)
            print(f"  {i} of {len(batches)} batches, {len(have):,} vectors, {spent:,} tokens")
    return (
        f"{plan}\nembedded {len(todo):,}, {spent:,} tokens as counted: ${spent / 1e6 * price:.4f}"
    )


def sample_inputs(inputs: BuildInputs, docs: set[str]) -> BuildInputs:
    """The build inputs narrowed to ``docs``, with every reference kept inside them."""
    paragraphs = [p for p in inputs.paragraphs if p.doc_id in docs]
    paras = {p.para_id for p in paragraphs}
    return BuildInputs(
        documents=[
            d if d.supersedes_doc_id in docs or d.supersedes_doc_id is None
            else d.model_copy(update={"supersedes_doc_id": None})
            for d in inputs.documents
            if d.doc_id in docs
        ],
        paragraphs=paragraphs,
        facets=[
            f for f in inputs.facets
            if (f.scope == "document" and f.ref_id in docs)
            or (f.scope == "paragraph" and f.ref_id in paras)
        ],
        entity_types=inputs.entity_types,
        chunks=[c for c in inputs.chunks if c.doc_id in docs],
        concordance=[
            e for e in inputs.concordance
            if (e.new_doc_id is None or e.new_doc_id in docs)
            and (e.new_para_id is None or e.new_para_id in paras)
        ],
        withdrawals=inputs.withdrawals,
        manifest={**inputs.manifest, "sample": read_sample()},
    )  # fmt: skip


def build_sample_schema(conn: psycopg.Connection, schema: str | None = None) -> str:
    commit, dirty = git_state()
    name = schema or make_schema_name("rbisample", date.today(), commit)
    chunk_all(DATA_DIR)
    docs = set(_sample_docs())
    inputs = sample_inputs(load_inputs(DATA_DIR, git_commit=commit, git_dirty=dirty), docs)
    vectors = {}
    for column, (vtype, identity) in SAMPLE_COLUMNS.items():
        path = cache_path(identity, VECTORS_DIR)
        if not path.exists():
            raise SystemExit(
                f"error: {path} is missing: run the embed step for {identity.model_id}"
            )
        vectors[column] = (vtype, identity, read_vectors(path, identity))
    hashes = {c.content_hash for c in inputs.chunks}
    for _column, (_, identity, have) in vectors.items():
        missing = hashes - set(have)
        if missing:
            raise SystemExit(
                f"error: {len(missing)} sample texts have no {identity.model_id} vector"
            )
    result = assemble(conn, name, inputs, HOSTED_256, baseline=None)
    with conn.transaction():
        for column, (vtype, identity, have) in vectors.items():
            conn.execute(
                sql.SQL("ALTER TABLE {}.chunks ADD COLUMN {} {}({})").format(
                    sql.Identifier(name), sql.Identifier(column), sql.SQL(vtype),
                    sql.Literal(identity.dim),
                )
            )  # fmt: skip
            conn.execute(
                "CREATE TEMP TABLE vec_stage (content_hash char(64), v text) ON COMMIT DROP"
            )
            with conn.cursor().copy("COPY vec_stage (content_hash, v) FROM STDIN") as copy:
                for h in sorted(hashes):
                    copy.write_row((h, halfvec_literal(have[h])))
            conn.execute(
                sql.SQL(
                    "UPDATE {}.chunks c SET {} = s.v::{}({}) FROM vec_stage s"
                    " WHERE s.content_hash = c.content_hash"
                ).format(sql.Identifier(name), sql.Identifier(column), sql.SQL(vtype),
                         sql.Literal(identity.dim))
            )  # fmt: skip
            conn.execute("DROP TABLE vec_stage")
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM {}.chunks WHERE {} IS NULL").format(
                    sql.Identifier(name), sql.Identifier(column)
                )
            ).fetchone()
            if row is None or row[0]:
                raise RuntimeError(f"{column}: chunks left without a vector")
    print(f"built {name}: {result.counts}")
    return name


def _embedder(model: str, dim: int, quantisation: str) -> tuple[Embedder, int | None]:
    from extant.index.settings import load_index_settings

    settings = load_index_settings()
    if model.startswith("openai/"):
        from extant.index.embed import OpenAIEmbedder

        if settings.openai_api_key is None:
            raise SystemExit("error: EXTANT_OPENAI_API_KEY is not set")
        e: Embedder = OpenAIEmbedder(
            settings.openai_api_key.get_secret_value(), model.removeprefix("openai/"), dim=dim
        )
        tpm: int | None = settings.embed_tokens_per_minute
    else:
        from evals.ablation.models import LocalEmbedder

        e, tpm = LocalEmbedder(model.removeprefix("local/"), dim, quantisation=quantisation), None
    e.identity = replace(e.identity, quantisation=quantisation)
    return e, tpm


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.ablation.sample_index")
    sub = parser.add_subparsers(dest="command", required=True)
    emb = sub.add_parser("embed", help="document vectors for the sample's chunks")
    emb.add_argument("--model", required=True)
    emb.add_argument("--dim", type=int, required=True)
    emb.add_argument("--quantisation", default="float16", choices=["float16", "float32"])
    emb.add_argument("--dry-run", action="store_true")
    bld = sub.add_parser("build", help="build the sample schema with the ablation columns")
    bld.add_argument("--schema")
    args = parser.parse_args(argv)
    if args.command == "embed":
        if args.dry_run:
            # No model needed to count: an identity stands in for the embedder.
            identity = ModelIdentity(args.model, args.dim, args.quantisation)
            print(embed_sample(_Counting(identity), tokens_per_minute=None, dry_run=True))
            return 0
        embedder, tpm = _embedder(args.model, args.dim, args.quantisation)
        print(embed_sample(embedder, tokens_per_minute=tpm))
        return 0
    from extant.index.settings import load_index_settings

    dsn = load_index_settings().writer_dsn.get_secret_value()
    with psycopg.connect(dsn, autocommit=True) as conn:
        build_sample_schema(conn, args.schema)
    return 0


class _Counting:
    def __init__(self, identity: ModelIdentity) -> None:
        self.identity = identity

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        raise AssertionError("a dry run embeds nothing")


if __name__ == "__main__":
    sys.exit(main())
