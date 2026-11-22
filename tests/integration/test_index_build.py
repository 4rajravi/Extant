"""Steps 6 to 8 against a real Postgres: the vector cache, assemble and verify."""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from extant.core.hashing import sha256_hex
from extant.core.identity import make_schema_name
from extant.core.schema import Connection, drop, schema_exists
from extant.index.assemble import AssembleError, BuildInputs, assemble
from extant.index.embed import (
    Embedded,
    EmbeddingError,
    HashEmbedder,
    Kind,
    ModelIdentity,
    cached_hashes,
    embed_missing,
    plan,
)
from extant.index.verify import BuildIncomplete, BuildRegression, table_counts
from tests.integration.synthetic import build_inputs

pytestmark = pytest.mark.integration


def texts(inputs: BuildInputs) -> dict[str, str]:
    return {c.content_hash: c.embed_text for c in inputs.chunks}


def embed_all(conn: Connection, inputs: BuildInputs, embedder: HashEmbedder) -> int:
    todo = plan(conn, texts(inputs), embedder.identity, eur_per_usd=0.9)
    return embed_missing(conn, embedder, todo.misses).embedded


def unique_name(corpus: str | None = None) -> str:
    return make_schema_name(
        corpus or f"t{secrets.token_hex(4)}", date.today(), secrets.token_hex(4)
    )


@pytest.fixture
def names(conn: Connection, bootstrapped: None) -> Iterator[list[str]]:
    """Schema names a test creates; dropped afterwards if they exist."""
    made: list[str] = []
    yield made
    for name in made:
        if schema_exists(conn, name):
            drop(conn, name)


def baseline_file(tmp_path: Path, **counts: int) -> Path:
    path = tmp_path / "baseline-counts.json"
    path.write_text(json.dumps(counts), encoding="utf-8")
    return path


def test_a_build_round_trips_into_a_scratch_schema_and_drops(
    conn: Connection, names: list[str]
) -> None:
    inputs = build_inputs()
    embedder = HashEmbedder()
    embed_all(conn, inputs, embedder)
    name = unique_name()
    names.append(name)
    result = assemble(conn, name, inputs, embedder.identity, baseline=None)
    assert result.counts["documents"] == len(inputs.documents)
    assert result.counts["paragraphs"] == len(inputs.paragraphs)
    assert result.counts["chunks"] == len(inputs.chunks) > len(inputs.paragraphs)
    manifest = dict(
        conn.execute(
            sql.SQL("SELECT key, value FROM {}.build_manifest").format(sql.Identifier(name))
        ).fetchall()
    )
    for key in ("corpus", "git_commit", "built_at", "embedding_model", "counts", "chunker_version"):
        assert key in manifest
    assert manifest["embedding_model"] == "test/hash-256"
    assert manifest["quantisation"] == "float16"
    drop(conn, name)
    assert not schema_exists(conn, name)
    assert cached_hashes(conn, embedder.identity) >= set(texts(inputs))


def test_a_second_build_with_no_input_change_makes_zero_embedding_calls(
    conn: Connection, names: list[str]
) -> None:
    inputs = build_inputs()
    embed_all(conn, inputs, HashEmbedder())
    again = HashEmbedder()
    assert embed_all(conn, inputs, again) == 0
    assert again.calls == 0
    name = unique_name()
    names.append(name)
    assemble(conn, name, inputs, again.identity, baseline=None)
    assert again.calls == 0


def test_a_model_change_misses_the_cache(conn: Connection, bootstrapped: None) -> None:
    inputs = build_inputs()
    embed_all(conn, inputs, HashEmbedder())
    other = ModelIdentity("test/another-model")
    assert set(plan(conn, texts(inputs), other, eur_per_usd=0.9).misses) == set(texts(inputs))
    wider = ModelIdentity("test/hash-256", quantisation="int8")
    assert set(plan(conn, texts(inputs), wider, eur_per_usd=0.9).misses) == set(texts(inputs))


class FailsOnSecondBatch(HashEmbedder):
    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        if self.calls == 1:
            self.calls += 1
            raise EmbeddingError("simulated outage")
        return super().embed(texts, kind=kind)


def test_an_interrupted_embed_keeps_finished_batches_and_nothing_else(
    conn: Connection, bootstrapped: None
) -> None:
    run = secrets.token_hex(8)
    misses = {sha256_hex(t): t for t in (f"text {run} {i}" for i in range(5))}
    embedder = FailsOnSecondBatch()
    try:
        with pytest.raises(EmbeddingError):
            embed_missing(conn, embedder, misses, batch_texts=2)
        have = cached_hashes(conn, embedder.identity) & set(misses)
        assert have == set(list(misses)[:2])
        resumed = HashEmbedder()
        embed_missing(conn, resumed, {h: t for h, t in misses.items() if h not in have})
        assert resumed.texts == 3
    finally:
        conn.execute("DELETE FROM public.vec_cache WHERE content_hash = ANY(%s)", (list(misses),))


def test_a_chunk_without_a_vector_fails_the_build_and_leaves_nothing(
    conn: Connection, names: list[str]
) -> None:
    inputs = build_inputs()
    embed_all(conn, inputs, HashEmbedder())
    stray = inputs.chunks[0]
    embed_text = f"never embedded {secrets.token_hex(8)}\n{stray.body}"
    unembedded = replace(stray, embed_text=embed_text, content_hash=sha256_hex(embed_text))
    broken = replace(inputs, chunks=[unembedded, *inputs.chunks[1:]])
    name = unique_name()
    names.append(name)
    with pytest.raises(AssembleError, match=r"1 of \d+ chunks have no test/hash-256 vector"):
        assemble(conn, name, broken, HashEmbedder().identity, baseline=None)
    assert not schema_exists(conn, name)


def test_verify_fails_a_build_whose_paragraphs_have_no_chunks(
    conn: Connection, names: list[str], tmp_path: Path
) -> None:
    inputs = build_inputs(unchunked=1)
    embed_all(conn, inputs, HashEmbedder())
    name = unique_name()
    names.append(name)
    with pytest.raises(BuildIncomplete, match="paragraphs_without_chunks"):
        assemble(conn, name, inputs, HashEmbedder().identity, baseline=baseline_file(tmp_path))
    assert not schema_exists(conn, name)


def test_the_first_build_is_checked_against_the_phase_1_baseline(
    conn: Connection, names: list[str], tmp_path: Path
) -> None:
    inputs = build_inputs()
    embed_all(conn, inputs, HashEmbedder())
    too_many = len(inputs.paragraphs) * 2
    name = unique_name()
    names.append(name)
    with pytest.raises(BuildRegression, match="paragraphs"):
        assemble(
            conn,
            name,
            inputs,
            HashEmbedder().identity,
            baseline=baseline_file(tmp_path, paragraphs=too_many, documents=5, concordance=1),
        )
    assert not schema_exists(conn, name)


def test_a_later_build_is_checked_against_the_previous_builds_manifest(
    conn: Connection, names: list[str], tmp_path: Path
) -> None:
    corpus = f"t{secrets.token_hex(4)}"
    full = build_inputs()
    embed_all(conn, full, HashEmbedder())
    first = unique_name(corpus)
    names.append(first)
    report = assemble(
        conn,
        first,
        full,
        HashEmbedder().identity,
        baseline=baseline_file(tmp_path, documents=5, paragraphs=50, concordance=1),
    ).verify
    assert report is not None
    assert report.compared_against.startswith("baseline")
    assert report.checks["chunks_not_unit_norm"] == 0

    smaller = build_inputs(drop_last=2)  # 20% of the paragraphs gone: a silent regression
    embed_all(conn, smaller, HashEmbedder())
    second = unique_name(corpus)
    names.append(second)
    with pytest.raises(
        BuildRegression, match=f"paragraphs: 40 rows, down from 50 in build {first}"
    ):
        assemble(conn, second, smaller, HashEmbedder().identity, baseline=tmp_path / "unused")
    assert not schema_exists(conn, second)
    assert table_counts(conn, first)["paragraphs"] == 50


def test_stored_vectors_are_unit_norm_at_256_dimensions(conn: Connection, names: list[str]) -> None:
    inputs = build_inputs()
    embed_all(conn, inputs, HashEmbedder())
    name = unique_name()
    names.append(name)
    assemble(conn, name, inputs, HashEmbedder().identity, baseline=None)
    row = conn.execute(
        sql.SQL(
            "SELECT min(vector_dims(embedding)), max(vector_dims(embedding)),"
            " min(l2_norm(embedding)), max(l2_norm(embedding)) FROM {}.chunks"
        ).format(sql.Identifier(name))
    ).fetchone()
    assert row is not None
    assert row[0] == row[1] == 256
    assert abs(row[2] - 1) < 1e-3
    assert abs(row[3] - 1) < 1e-3


def test_fts_is_built_from_body_and_holds_no_embed_text_header_terms(
    conn: Connection, names: list[str]
) -> None:
    inputs = build_inputs()
    embed_all(conn, inputs, HashEmbedder())
    name = unique_name()
    names.append(name)
    assemble(conn, name, inputs, HashEmbedder().identity, baseline=None)
    s = sql.Identifier(name)
    for header_word in ("zebra", "quokka", "narwhal", "housing", "applies"):
        in_header = conn.execute(
            sql.SQL("SELECT count(*) FROM {}.chunks WHERE embed_text ILIKE %s").format(s),
            (f"%{header_word}%",),
        ).fetchone()
        in_fts = conn.execute(
            sql.SQL("SELECT count(*) FROM {}.chunks WHERE fts @@ to_tsquery('english', %s)").format(
                s
            ),
            (header_word,),
        ).fetchone()
        assert in_header is not None
        assert in_fts is not None
        assert in_header[0] > 0, header_word
        assert in_fts[0] == 0, header_word
    same = conn.execute(
        sql.SQL("SELECT count(*) FROM {}.chunks WHERE fts <> to_tsvector('english', body)").format(
            s
        )
    ).fetchone()
    assert same == (0,)


def test_the_reader_role_can_query_a_build_and_cannot_write_to_it(
    conn: Connection, names: list[str], reader_dsn: str
) -> None:
    inputs = build_inputs()
    embed_all(conn, inputs, HashEmbedder())
    name = unique_name()
    names.append(name)
    assemble(conn, name, inputs, HashEmbedder().identity, baseline=None)
    with psycopg.connect(reader_dsn, autocommit=True) as reader:
        reader.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(name)))
        count = reader.execute("SELECT count(*) FROM chunks").fetchone()
        assert count == (len(inputs.chunks),)
        for statement in (
            "DELETE FROM chunks",
            "UPDATE paragraphs SET body = 'x'",
            "INSERT INTO withdrawals SELECT * FROM withdrawals",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(statement)
