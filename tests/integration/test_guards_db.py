"""The guards that need Postgres: the cap counter, the serving role's grants on it, readiness,
and the lexical only rung on a real build."""

from __future__ import annotations

import secrets
from collections.abc import Iterator, Sequence
from datetime import date, timedelta

import psycopg
import pytest
from psycopg import sql

from extant.agent.index import PgIndex
from extant.agent.service import readiness
from extant.core.identity import make_schema_name
from extant.core.schema import drop, schema_exists
from extant.guards.middleware import CallRecord, PgUsageStore, RequestRecord
from extant.index.assemble import assemble
from extant.index.embed import Embedded, EmbeddingError, HashEmbedder, Kind, embed_missing, plan
from tests.integration.synthetic import build_inputs

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def schema(writer_dsn: str, bootstrapped: None) -> Iterator[str]:
    inputs = build_inputs()
    name = make_schema_name("test", date.today(), secrets.token_hex(4))
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        e = HashEmbedder()
        todo = plan(
            c, {x.content_hash: x.embed_text for x in inputs.chunks}, e.identity, eur_per_usd=0.9
        )
        embed_missing(c, e, todo.misses)
        assemble(c, name, inputs, e.identity, baseline=None)
    yield name
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        if schema_exists(c, name):
            drop(c, name)


@pytest.fixture
def day(writer_dsn: str, bootstrapped: None) -> Iterator[date]:
    """A day far in the future, unique to the test, cleaned up afterwards."""
    d = date(2099, 1, 1) + timedelta(days=secrets.randbelow(30_000))
    yield d
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        c.execute("DELETE FROM public.request_ledger WHERE day = %s", (d,))
        c.execute("DELETE FROM public.usage_daily WHERE day = %s", (d,))


def _request(day: date, outcome: str = "answer") -> RequestRecord:
    return RequestRecord(
        request_id=secrets.token_hex(8),
        day=day,
        index_version="idx_test_20991231_abcdef1",
        query_sha256="0" * 64,
        outcome=outcome,
        refusal_reason=None if outcome == "answer" else "no_support_found",
        model_calls=2,
        eur=0.0123,
    )


def test_the_cap_counter_survives_a_restart(writer_dsn: str, day: date) -> None:
    with psycopg.connect(writer_dsn, autocommit=True) as first:
        store = PgUsageStore(first, cap=50)
        store.record_request(_request(day))
        store.record_request(_request(day, "refusal"))
        assert store.answered(day) == 1
    # A cold start: a new process, a new connection, nothing in memory.
    with psycopg.connect(writer_dsn, autocommit=True) as second:
        assert PgUsageStore(second, cap=50).answered(day) == 1
        row = second.execute(
            "SELECT requests, answered FROM public.usage_daily WHERE day = %s", (day,)
        ).fetchone()
        assert row == (2, 1)


def test_the_serving_role_can_move_the_counter_and_nothing_else(reader_dsn: str, day: date) -> None:
    with psycopg.connect(reader_dsn, autocommit=True) as conn:
        store = PgUsageStore(conn, cap=50)
        store.record_call(day, CallRecord("claude-haiku-5-5", 10, 1, 0.0001, 5.0, 1, ()))
        store.record_request(_request(day))
        assert store.answered(day) == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM public.usage_daily WHERE day = %s", (day,))


def test_the_ledger_has_no_column_for_the_question(writer_dsn: str, bootstrapped: None) -> None:
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        cols = {
            r[0]
            for r in c.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = 'request_ledger'"
            ).fetchall()
        }
    assert "query_sha256" in cols
    assert not cols & {"query", "question", "raw_query", "query_redacted"}


def test_readiness_serves_only_a_verified_build_with_every_migration(
    writer_dsn: str, schema: str
) -> None:
    model = HashEmbedder().identity.model_id
    manifest = sql.SQL("UPDATE {}.build_manifest SET value = %s::jsonb WHERE key = %s").format(
        sql.Identifier(schema)
    )
    verified = '{"checks": {"chunks_not_unit_norm": 0, "paragraphs_without_chunks": 0}}'
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        assert not readiness(c, "idx_test_20991231_0000000", model, None).ready  # missing
        unverified = readiness(c, schema, model, None)  # assembled with no baseline
        assert unverified.blocking == [f"{schema} has no verification record in its manifest"]
        with c.transaction(force_rollback=True):
            c.execute(
                sql.SQL("INSERT INTO {}.build_manifest VALUES ('verify', %s::jsonb)").format(
                    sql.Identifier(schema)
                ),
                (verified,),
            )
            ok = readiness(c, schema, model, cache=None)
            assert ok.ready, ok.blocking
            assert ok.warnings == [
                "no example cache: the cap and an outage would serve the static page"
            ]
            c.execute(manifest, ('["0001_initial", "0002_source_anchor"]', "migrations"))
            old = readiness(c, schema, model, None)
            assert not old.ready
            assert "was built with" in old.blocking[0]


class _Failing:
    """An embedder whose API is down: every call times out."""

    def __init__(self) -> None:
        self.identity = HashEmbedder().identity

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        raise EmbeddingError("timed out embedding the query")


def test_the_embedding_api_down_falls_back_to_lexical_only(reader_dsn: str, schema: str) -> None:
    with psycopg.connect(reader_dsn, autocommit=True) as conn:
        found = PgIndex(conn, schema, _Failing()).search(
            "dividend", ("cb",), date(2026, 6, 1), validity="in_force", top_n=6
        )
    assert found.degraded == ["lexical_only"]
    assert found.candidates
    assert found.embedding_calls == 0
