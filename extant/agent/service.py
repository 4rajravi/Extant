"""The service: the one entry point for a question, and the degradation ladder around the
graph.

    admission (redact) -> cap -> graph -> record the request -> log -> response

Admission redacts the question before anything else sees it: the state object, the
embedding call, every model and the log all get the redacted form (``AgentState`` refuses an
unredacted one). The response says what kind of identifier was removed.

The ladder, each rung a worse but honest answer rather than an error, and each marked in the
response's ``degraded`` list:

1. Full path: hybrid retrieval, a composed answer, verified citations.
2. No rerank: not a rung here. Serving has no reranker (Phase 3 measured one worse), so its
   absence is the normal state, not a degradation.
3. Retrieval only (``retrieval_only``): a model failed after its one retry; the graph shows
   the retrieved provisions whole, with no prose. Lexical only retrieval
   (``lexical_only``) when the embedding call fails is the same rung's retrieval half.
4. Cached examples (``cached_examples``): Postgres unreachable after one retry, or the daily
   cap reached. Zero model calls.
5. Static page (``static_page``): no usable example cache either.

The cap is checked here before the graph runs, and again by the middleware before every
model call: the first saves a request that would only be refused, the second binds calls
this file never sees. A counter that cannot be read counts as unreachable Postgres.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from psycopg import sql

from extant.agent.examples import STATIC_PAGE, ExampleCache
from extant.agent.graph import ask, build
from extant.agent.llm import StructuredModel
from extant.agent.models import (
    AgentState,
    Answer,
    AskResponse,
    Refusal,
    RefusalReason,
    RetrievalOnly,
    Unavailable,
)
from extant.agent.runtime import Deps, Index
from extant.core.migrations import index_migrations
from extant.core.schema import schema_exists
from extant.guards import logs, pii
from extant.guards.errors import CapReachedError
from extant.guards.middleware import Ledger, RequestRecord, UsageStore, ledger, today_in_india

DATABASE_ERRORS: tuple[type[Exception], ...] = (psycopg.OperationalError, psycopg.InterfaceError)


@dataclass(frozen=True)
class Served:
    response: AskResponse
    state: AgentState | None  # None when the graph never ran (cap, database)
    record: dict[str, Any]  # the log record


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Service:
    index_version: str
    open_index: Callable[[], Index]  # a fresh index per attempt; raises a database error
    models: Mapping[str, StructuredModel]  # guarded: AgentSettings.models(store)
    store: UsageStore
    entities: Mapping[str, str]
    cache: ExampleCache | None = None
    eur_per_usd: float = 0.90
    log: Callable[[Mapping[str, Any]], None] = logs.emit
    day: Callable[[], date] = today_in_india
    now: Callable[[], datetime] = _utcnow
    on_database_error: Callable[[], None] | None = None  # e.g. drop a broken connection

    def ask(
        self,
        question: str,
        *,
        entity: str | None = None,
        as_of: date | None = None,
        request_id: str | None = None,
    ) -> AskResponse:
        return self.run(question, entity=entity, as_of=as_of, request_id=request_id).response

    def run(
        self,
        question: str,
        *,
        entity: str | None = None,
        as_of: date | None = None,
        request_id: str | None = None,
    ) -> Served:
        clean = pii.redact(question)  # before anything else sees the question
        rid = request_id or uuid.uuid4().hex
        start = time.perf_counter()
        today = self.day()
        state: AgentState | None = None
        with ledger() as led:
            try:
                answered = self.store.answered(today)
                if answered >= self.store.cap:
                    raise CapReachedError(f"{answered} of {self.store.cap} answered on {today}")
                state = self._graph(clean.text, entity, as_of, today, rid)
                if state.response is None:
                    raise RuntimeError("the graph ended without a response")
                response = state.response
            except CapReachedError:
                state = None
                response = self._capped(entity, as_of, today, start, led)
            except DATABASE_ERRORS:
                state = None
                response = self._unavailable(entity, as_of, today, start, led)
        response = response.model_copy(
            update={"redactions": list(clean.kinds), "notice": pii.notice(clean.kinds)}
        )
        self._record(rid, today, clean.text, response, led)
        record = logs.request_record(
            at=self.now(),
            request_id=rid,
            query_redacted=clean.text,
            response=response,
            state=state,
            model_calls=len(led.calls),
        )
        self.log(record)
        return Served(response, state, record)

    # ----------------------------------------------------------------------- the graph

    def _graph(
        self, text: str, entity: str | None, as_of: date | None, today: date, rid: str
    ) -> AgentState:
        for attempt in (1, 2):  # Postgres: retry once, then the cached examples
            try:
                deps = Deps(
                    models=self.models,
                    index=self.open_index(),
                    eur_per_usd=self.eur_per_usd,
                    entities=self.entities,
                )
                return ask(
                    build(deps),
                    text,
                    index_version=self.index_version,
                    entity=entity,
                    as_of=as_of,
                    today=today,
                    request_id=rid,
                )
            except DATABASE_ERRORS:
                if self.on_database_error is not None:
                    self.on_database_error()
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    # ----------------------------------------------------------------------- the ladder

    def _examples(self) -> tuple[list[Any], list[str]]:
        if self.cache is None:
            return [], ["static_page"]
        degraded = ["cached_examples"]
        if self.cache.stale(self.index_version):
            degraded.append("stale_examples")
        return list(self.cache.examples), degraded

    def _response(
        self,
        result: Refusal | Unavailable,
        entity: str | None,
        as_of: date | None,
        today: date,
        start: float,
        led: Ledger,
        degraded: list[str],
    ) -> AskResponse:
        return AskResponse(
            result=result,
            as_of=as_of or today,
            entity_type=entity,
            index_version=self.index_version,
            retrieved=[],
            dropped=[],
            degraded=degraded,
            cost_eur=round(led.eur, 6),
            latency_ms=round((time.perf_counter() - start) * 1000),
        )

    def _capped(
        self, entity: str | None, as_of: date | None, today: date, start: float, led: Ledger
    ) -> AskResponse:
        shown, degraded = self._examples()
        message = (
            f"Extant has answered its {self.store.cap} questions for {today.isoformat()}, its"
            " daily limit, and answers again from midnight India time."
        )
        if shown:
            message += (
                " Below are answers it gave earlier to example questions. They are not answers"
                " to your question."
            )
        result = Refusal(
            reason=RefusalReason.CAP_REACHED, message=message, nearest=[], examples=shown
        )
        return self._response(result, entity, as_of, today, start, led, degraded)

    def _unavailable(
        self, entity: str | None, as_of: date | None, today: date, start: float, led: Ledger
    ) -> AskResponse:
        shown, degraded = self._examples()
        if shown:
            message = (
                "Extant cannot reach its index right now, so it cannot answer your question."
                " Below are answers it gave earlier to example questions. They are not answers"
                " to your question."
            )
        else:
            message = STATIC_PAGE
        result = Unavailable(message=message, examples=shown)
        return self._response(result, entity, as_of, today, start, led, degraded)

    # ----------------------------------------------------------------------- accounting

    def _record(self, rid: str, day: date, text: str, response: AskResponse, led: Ledger) -> None:
        result = response.result
        # If the counter is unreachable this request is not counted. Only an answer moves the
        # cap, and an answer needed the database a moment ago; the log line remains.
        with contextlib.suppress(*DATABASE_ERRORS):
            self.store.record_request(
                RequestRecord(
                    request_id=rid,
                    day=day,
                    index_version=self.index_version,
                    query_sha256=logs.query_sha256(text),
                    outcome=result.outcome,
                    refusal_reason=result.reason.value if isinstance(result, Refusal) else None,
                    model_calls=len(led.calls),
                    eur=led.eur,
                )
            )


def answered(response: AskResponse) -> bool:
    return isinstance(response.result, Answer)


def retrieval_only(response: AskResponse) -> bool:
    return isinstance(response.result, RetrievalOnly)


# --------------------------------------------------------------------------- readiness


@dataclass(frozen=True)
class Readiness:
    blocking: list[str]  # any of these and the container never receives traffic
    warnings: list[str]  # served anyway, and reported

    @property
    def ready(self) -> bool:
        return not self.blocking


def readiness(
    conn: psycopg.Connection, schema: str, embedding_model: str, cache: ExampleCache | None
) -> Readiness:
    """Whether this container may serve ``schema``. Run at start: a container that is not
    ready never receives traffic, and the previous revision keeps serving. The example cache
    only warns: it is the fallback, and a stale one is still an honest set of examples."""
    if not schema_exists(conn, schema):
        return Readiness([f"index schema {schema} does not exist"], [])
    rows: dict[str, Any] = dict(
        conn.execute(
            sql.SQL("SELECT key, value FROM {}.build_manifest").format(sql.Identifier(schema))
        ).fetchall()
    )
    problems = []
    expected = [m.name for m in index_migrations()]
    if rows.get("migrations") != expected:
        problems.append(f"{schema} was built with {rows.get('migrations')}, not {expected}")
    checks = (rows.get("verify") or {}).get("checks")
    if not checks:
        problems.append(f"{schema} has no verification record in its manifest")
    elif any(v for v in checks.values()):
        failed = sorted(k for k, v in checks.items() if v)
        problems.append(f"{schema} failed verification: {failed}")
    if rows.get("embedding_model") != embedding_model:
        problems.append(
            f"{schema} was embedded with {rows.get('embedding_model')}, serving uses"
            f" {embedding_model}"
        )
    warnings = []
    if cache is None:
        warnings.append("no example cache: the cap and an outage would serve the static page")
    elif cache.stale(schema):
        warnings.append(f"the example cache was built on {cache.index_version}, not {schema}")
    return Readiness(problems, warnings)
