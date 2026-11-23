"""The HTTP API on ``PgRuntime`` and its pool, against a real build of the synthetic corpus,
connected as the read only role: citations through aliases and the alias counter, the daily
cap read from its Postgres row, redaction before anything the graph touches, readiness on a
tampered manifest, and the ladder with the database gone."""

from __future__ import annotations

import secrets
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from extant.agent.examples import ExampleCache
from extant.agent.serving import Serving, make_pool
from extant.api.app import create_app
from extant.api.runtime import PgRuntime
from extant.api.settings import ApiSettings
from extant.core.identity import make_schema_name
from extant.core.schema import drop, schema_exists
from extant.guards.middleware import today_in_india
from extant.index.assemble import assemble
from extant.index.embed import Embedded, HashEmbedder, Kind, embed_missing, plan
from tests.agent_support import composer_says, judge_says, parse_says
from tests.integration.synthetic import D1V1, D4, build_inputs

pytestmark = pytest.mark.integration

RETIRED = "rbi:md:2025:0004:v1#p77"
OUT_OF_SCOPE = "What is the best recipe for masala chai?"  # refused by pattern, no model


def _build(writer_dsn: str) -> str:
    inputs = build_inputs()
    name = make_schema_name("test", date.today(), secrets.token_hex(4))
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        e = HashEmbedder()
        todo = plan(
            c, {x.content_hash: x.embed_text for x in inputs.chunks}, e.identity, eur_per_usd=0.9
        )
        embed_missing(c, e, todo.misses)
        assemble(c, name, inputs, e.identity, baseline=None)
    return name


def _drop(writer_dsn: str, name: str) -> None:
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        if schema_exists(c, name):
            drop(c, name)
        c.execute("DELETE FROM public.para_id_alias_hits WHERE index_version = %s", (name,))


@pytest.fixture(scope="module")
def schema(writer_dsn: str, bootstrapped: None) -> Iterator[str]:
    name = _build(writer_dsn)
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        c.execute(
            sql.SQL(
                "INSERT INTO {}.para_id_aliases (old_para_id, new_para_id, retired_in, reason)"
                " VALUES (%s, %s, 'idx_test_old', 'renumbered by the parser')"
            ).format(sql.Identifier(name)),
            (RETIRED, f"{D4.doc_id}#p3"),
        )
    yield name
    _drop(writer_dsn, name)


class WatchedEmbedder:
    def __init__(self) -> None:
        self.inner = HashEmbedder()
        self.identity = self.inner.identity
        self.texts: list[str] = []

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        self.texts.extend(texts)
        return self.inner.embed(texts, kind=kind)


class Never:
    """A model that must not be called."""

    model = "never"

    def complete(self, schema: Any, *, system: str, user: str) -> Any:
        raise AssertionError("a model was called")


def _runtime(
    dsn: str,
    schema: str,
    *,
    models: dict[str, Any] | None = None,
    embedder: Any = None,
    cap: int = 2,
    logs: list[Any] | None = None,
    **pool: Any,
) -> PgRuntime:
    chosen = models or {"parse": Never(), "compose": Never(), "judge": Never()}
    serving = Serving(
        pool=make_pool(dsn, **pool),
        schema=schema,
        cap=cap,
        embedder=embedder or HashEmbedder(),
        models=lambda store: chosen,
        eur_per_usd=0.9,
        cache=ExampleCache.load(),
    )
    return PgRuntime(serving, log=(logs.append if logs is not None else (lambda r: None)))


@pytest.fixture
def make(reader_dsn: str) -> Iterator[Any]:
    made: list[PgRuntime] = []

    def build(schema: str, **kw: Any) -> TestClient:
        runtime = _runtime(kw.pop("dsn", reader_dsn), schema, **kw)
        made.append(runtime)
        return TestClient(create_app(runtime, settings=ApiSettings()))

    yield build
    for runtime in made:
        runtime.close()


# --------------------------------------------------------------------------- citations


def _alias_hits(writer_dsn: str, schema: str) -> int:
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        row = c.execute(
            "SELECT hits FROM public.para_id_alias_hits WHERE old_para_id = %s"
            " AND index_version = %s",
            (RETIRED, schema),
        ).fetchone()
    return int(row[0]) if row else 0


def test_a_live_id_resolves(make: Any, schema: str) -> None:
    r = make(schema).get(f"/paragraph/{D1V1.doc_id}%23p1")
    assert r.status_code == 200
    body = r.json()
    assert (body["resolved_through_alias"], body["index_version"]) == (False, schema)
    assert body["paragraph"]["in_force_to"] == "2026-01-01"


def test_a_retired_id_resolves_through_its_alias_says_so_and_is_counted(
    make: Any, schema: str, writer_dsn: str
) -> None:
    before = _alias_hits(writer_dsn, schema)
    c = make(schema)
    r = c.get(f"/paragraph/{RETIRED.replace('#', '%23')}")
    assert r.status_code == 200
    body = r.json()
    assert body["resolved_through_alias"] is True
    assert body["requested"] == RETIRED
    assert body["paragraph"]["para_id"] == f"{D4.doc_id}#p3"
    assert "retired" in body["note"]
    c.get(f"/paragraph/{RETIRED.replace('#', '%23')}")
    assert _alias_hits(writer_dsn, schema) == before + 2


def test_an_id_with_neither_a_row_nor_an_alias_is_404(make: Any, schema: str) -> None:
    r = make(schema).get(f"/paragraph/{D4.doc_id}%23p404")
    assert r.status_code == 404
    assert r.json()["index_version"] == schema


# --------------------------------------------------------------------------- the cap


@pytest.fixture
def todays_row(writer_dsn: str) -> Iterator[Any]:
    """Set today's counter directly, and put back whatever was there."""
    day = today_in_india()
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        saved = c.execute(
            "SELECT requests, answered, model_calls, model_eur FROM public.usage_daily"
            " WHERE day = %s",
            (day,),
        ).fetchone()

    def set_answered(n: int) -> None:
        with psycopg.connect(writer_dsn, autocommit=True) as c:
            c.execute(
                "INSERT INTO public.usage_daily (day, answered) VALUES (%s, %s)"
                " ON CONFLICT (day) DO UPDATE SET answered = EXCLUDED.answered",
                (day, n),
            )

    yield set_answered
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        if saved is None:
            c.execute("DELETE FROM public.usage_daily WHERE day = %s", (day,))
        else:
            c.execute(
                "UPDATE public.usage_daily SET requests = %s, answered = %s, model_calls = %s,"
                " model_eur = %s WHERE day = %s",
                (*saved, day),
            )


def test_the_cap_is_read_from_its_postgres_row(make: Any, schema: str, todays_row: Any) -> None:
    c = make(schema, cap=2)
    todays_row(0)
    open_ = c.post("/ask", json={"question": OUT_OF_SCOPE, "entity": "cb"})
    assert open_.json()["result"]["reason"] == "out_of_scope"
    assert "Retry-After" not in open_.headers
    todays_row(2)  # no request moved it: only the row did
    capped = c.post("/ask", json={"question": OUT_OF_SCOPE, "entity": "cb"})
    assert capped.status_code == 200
    result = capped.json()["result"]
    assert (result["outcome"], result["reason"]) == ("refusal", "cap_reached")
    assert result["examples"]
    assert "Retry-After" in capped.headers
    health = c.get("/health").json()
    assert health["daily_cap"]["remaining"] == 0


# --------------------------------------------------------------------------- admission


def test_the_graph_never_receives_the_identifier(make: Any, schema: str) -> None:
    parse = parse_says(entity_type="cb", topic_terms=["dividend"])
    compose = composer_says(insufficient=True, missing="nothing on dividends here")
    judge = judge_says()
    embedder = WatchedEmbedder()
    logs: list[Any] = []
    c = make(
        schema,
        models={"parse": parse, "compose": compose, "judge": judge},
        embedder=embedder,
        logs=logs,
    )
    question = "My PAN is ABCPE1234F: what records must a commercial bank keep on dividends?"
    r = c.post("/ask", json={"question": question, "entity": "cb", "as_of": "2026-06-01"})
    assert r.status_code == 200
    assert r.json()["redactions"] == ["PAN"]
    prompts = [user for m in (parse, compose, judge) for _, _, user in m.calls]
    seen = prompts + embedder.texts + [str(record) for record in logs]
    assert parse.calls  # the graph ran
    assert embedder.texts  # and embedded
    assert all("ABCPE1234F" not in text for text in seen)


# --------------------------------------------------------------------------- readiness, the ladder


@pytest.fixture
def tampered(writer_dsn: str, bootstrapped: None) -> Iterator[str]:
    name = _build(writer_dsn)
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        c.execute(
            sql.SQL("UPDATE {}.build_manifest SET value = %s WHERE key = 'migrations'").format(
                sql.Identifier(name)
            ),
            (psycopg.types.json.Jsonb(["0001_initial"]),),
        )
    yield name
    _drop(writer_dsn, name)


def test_a_failed_manifest_check_is_not_ready_while_liveness_passes(
    make: Any, tampered: str
) -> None:
    c = make(tampered)
    r = c.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert (body["status"], body["database"], body["manifest_ok"]) == (
        "not_ready",
        "reachable",
        False,
    )
    assert any("0001_initial" in p for p in body["problems"])
    assert c.get("/health/live").status_code == 200


def test_with_the_database_gone_examples_and_liveness_still_answer(make: Any) -> None:
    gone = "postgresql://extant_reader:x@127.0.0.1:1/extant"
    c = make("idx_rbi_20261123_c23c1e8", dsn=gone, timeout=0.5, connect_timeout=1)
    examples = c.get("/examples")  # the models are Never: any call would raise
    assert examples.status_code == 200
    assert examples.json()["examples"]
    assert c.get("/health/live").status_code == 200
    health = c.get("/health")
    assert (health.status_code, health.json()["database"]) == (503, "unreachable")
    ladder = c.post("/ask", json={"question": OUT_OF_SCOPE, "entity": "cb"})
    assert ladder.status_code == 200
    assert ladder.json()["result"]["outcome"] == "unavailable"
    assert ladder.json()["result"]["examples"]
    assert c.get(f"/paragraph/{D1V1.doc_id}%23p1").status_code == 503
