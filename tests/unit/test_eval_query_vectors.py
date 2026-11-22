"""The committed query vector cache: byte stable, identity checked, and a miss is loud."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.query_vectors import (
    CachedQueryEmbedder,
    MissingQueryVectorError,
    cache_path,
    query_hash,
    read_vectors,
    refresh,
    write_vectors,
)
from extant.index.embed import HashEmbedder, ModelIdentity


def test_the_file_is_named_for_the_configuration() -> None:
    identity = ModelIdentity("openai/text-embedding-3-large", 256, "float16")
    assert (
        cache_path(identity, Path("/x")).name == "openai-text-embedding-3-large_256_float16.parquet"
    )


def test_vectors_round_trip_at_float16_precision(tmp_path: Path) -> None:
    e = HashEmbedder()
    v = e.embed(["a query"], kind="query").vectors[0]
    path = tmp_path / "v.parquet"
    write_vectors(path, e.identity, {query_hash("a query"): v})
    (back,) = read_vectors(path, e.identity).values()
    assert len(back) == 256
    assert max(abs(x - y) for x, y in zip(v, back, strict=True)) < 1e-3


def test_the_same_vectors_write_the_same_bytes(tmp_path: Path) -> None:
    e = HashEmbedder()
    vectors = {query_hash(t): e.embed([t], kind="query").vectors[0] for t in ("b", "a", "c")}
    write_vectors(tmp_path / "1.parquet", e.identity, vectors)
    write_vectors(tmp_path / "2.parquet", e.identity, dict(reversed(list(vectors.items()))))
    assert (tmp_path / "1.parquet").read_bytes() == (tmp_path / "2.parquet").read_bytes()


def test_a_file_for_another_configuration_is_refused(tmp_path: Path) -> None:
    e = HashEmbedder()
    write_vectors(tmp_path / "v.parquet", e.identity, {query_hash("q"): [0.0] * 256})
    other = ModelIdentity("test/hash-256", 256, "float32")
    with pytest.raises(ValueError, match="holds vectors for"):
        read_vectors(tmp_path / "v.parquet", other)


def test_a_missing_query_vector_fails_loudly_naming_the_query(tmp_path: Path) -> None:
    e = HashEmbedder()
    refresh(["known query"], e, directory=tmp_path)
    cached = CachedQueryEmbedder(e.identity, tmp_path)
    assert cached.embed(["known query"], kind="query").tokens == 0
    with pytest.raises(MissingQueryVectorError, match="'an unknown query'"):
        cached.embed(["an unknown query"], kind="query")


def test_a_missing_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(MissingQueryVectorError, match="--refresh"):
        CachedQueryEmbedder(HashEmbedder().identity, tmp_path)


def test_the_cache_holds_queries_only(tmp_path: Path) -> None:
    e = HashEmbedder()
    refresh(["q"], e, directory=tmp_path)
    with pytest.raises(ValueError, match="queries only"):
        CachedQueryEmbedder(e.identity, tmp_path).embed(["q"], kind="document")


class Counting(HashEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def embed(self, texts, *, kind="document"):  # type: ignore[no-untyped-def]
        self.seen.extend(texts)
        return super().embed(texts, kind=kind)


def test_refresh_keeps_committed_vectors_embeds_new_ones_and_drops_stale(tmp_path: Path) -> None:
    first = Counting()
    refresh(["a", "b"], first, directory=tmp_path)
    assert sorted(first.seen) == ["a", "b"]
    second = Counting()
    report = refresh(["b", "c"], second, directory=tmp_path)
    assert second.seen == ["c"]  # "b" was kept, not re-embedded
    assert (report.kept, report.embedded, report.dropped) == (1, 1, 1)
    held = read_vectors(cache_path(second.identity, tmp_path), second.identity)
    assert set(held) == {query_hash("b"), query_hash("c")}
    forced = Counting()
    refresh(["b", "c"], forced, directory=tmp_path, force=True)
    assert sorted(forced.seen) == ["b", "c"]
