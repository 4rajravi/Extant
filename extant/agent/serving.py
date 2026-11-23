"""Serving: the configured build behind a ``Service``, connected as the read only role.

Added in Phase 7 for the MCP server, and meant for any serving entry point (the HTTP API in
a later phase): it is wiring, not logic, and it lives here so no entry point grows its own.

``start`` runs once per process. A build is immutable, so what it reads then (readiness, the
entity labels, the example cache) holds for the life of the process; a new build is a new
process. ``service`` and ``connection`` run once per request and close every connection they
opened when the request ends, so a process holds no connection between requests and an
outage reaches the Service's ladder as the database error it already handles. The usage
store connects on first use for the same reason: a store that cannot connect is a counter
that cannot be read, which the Service and the middleware treat as unreachable Postgres.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
from psycopg import sql

from extant.agent.examples import ExampleCache, ExampleCacheError
from extant.agent.index import PgIndex
from extant.agent.service import Readiness, Service, readiness
from extant.agent.settings import AgentSettings, load_agent_settings
from extant.guards import logs
from extant.guards.middleware import CallRecord, PgUsageStore, RequestRecord
from extant.index.embed import Embedder

CONNECT_TIMEOUT_SECONDS = 5


class NotReadyError(RuntimeError):
    """The configured build may not be served (see ``extant.agent.service.readiness``)."""


@dataclass(frozen=True)
class Serving:
    dsn: str  # the read only role's; never logged
    schema: str
    cap: int
    embedder: Embedder
    agent: AgentSettings
    entities: Mapping[str, str]  # code -> label, from the build
    cache: ExampleCache | None
    readiness: Readiness


def _open(dsn: str, schema: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    return conn


def start() -> Serving:
    """Load the settings, check the build may be served, and read what never changes."""
    from extant.core.settings import load_settings
    from extant.index.settings import load_index_settings

    settings = load_settings()
    embedder = load_index_settings().embedder()
    try:
        cache: ExampleCache | None = ExampleCache.load()
    except ExampleCacheError:
        cache = None
    dsn = settings.reader_dsn.get_secret_value()
    with _open(dsn, settings.build_schema) as conn:
        ready = readiness(conn, settings.build_schema, embedder.identity.model_id, cache)
        if not ready.ready:
            raise NotReadyError("; ".join(ready.blocking))
        rows = conn.execute("SELECT code, label FROM entity_types ORDER BY code").fetchall()
    return Serving(
        dsn=dsn,
        schema=settings.build_schema,
        cap=settings.daily_answer_cap,
        embedder=embedder,
        agent=load_agent_settings(),
        entities={str(c): str(label) for c, label in rows},
        cache=cache,
        readiness=ready,
    )


@contextlib.contextmanager
def connection(serving: Serving) -> Iterator[psycopg.Connection]:
    """One read only connection on the build, for one request."""
    with _open(serving.dsn, serving.schema) as conn:
        yield conn


class _LazyStore:
    """``PgUsageStore`` on a connection opened at first use."""

    def __init__(self, connect: Callable[[], psycopg.Connection], cap: int) -> None:
        self._connect = connect
        self._store: PgUsageStore | None = None
        self.cap = cap

    def _get(self) -> PgUsageStore:
        if self._store is None:
            self._store = PgUsageStore(self._connect(), self.cap)
        return self._store

    def answered(self, day: date) -> int:
        return self._get().answered(day)

    def record_call(self, day: date, call: CallRecord) -> None:
        self._get().record_call(day, call)

    def record_request(self, request: RequestRecord) -> None:
        self._get().record_request(request)


@contextlib.contextmanager
def service(
    serving: Serving, *, log: Callable[[Mapping[str, Any]], None] = logs.emit
) -> Iterator[Service]:
    """A Service for one request: real models behind the middleware, the daily cap counted in
    Postgres, a fresh index connection per attempt, every connection closed at the end."""
    opened: list[psycopg.Connection] = []

    def connect() -> psycopg.Connection:
        conn = _open(serving.dsn, serving.schema)
        opened.append(conn)
        return conn

    store = _LazyStore(connect, serving.cap)
    try:
        yield Service(
            index_version=serving.schema,
            open_index=lambda: PgIndex(connect(), serving.schema, serving.embedder),
            models=serving.agent.models(store),
            store=store,
            entities=serving.entities,
            cache=serving.cache,
            eur_per_usd=serving.agent.eur_per_usd,
            log=log,
        )
    finally:
        for conn in opened:
            with contextlib.suppress(psycopg.Error):
                conn.close()
