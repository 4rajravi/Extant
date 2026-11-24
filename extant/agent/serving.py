"""Serving: the configured build behind a ``Service``, connected as the read only role.

Shared by the entry points (the MCP server, the HTTP API). It is wiring, not logic, and it
lives here so no entry point grows its own.

``start`` runs once per process. A build is immutable, so what is read from it once (the
entity labels) holds for the life of the process; a new build is a new process. The process
keeps one connection pool, sized for this one container: instances are independent, so a
pool sized for the whole service would be multiplied by the instance count.

Every connection goes through the pool, and every unit of work runs inside ``in_build``:
one transaction with the build named by ``SET LOCAL``. That is what holds through Neon's
transaction-mode pooler, which silently drops a session ``SET search_path`` (see
``extant.core.schema.in_build``). Prepared statements are off for the same reason: a
statement prepared on one server connection does not exist on the next.

Timeouts are short on purpose. A database waking from autosuspend, or gone, must reach the
Service's ladder (one retry, then the cached examples) within seconds, not hang the request.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator, Mapping
from datetime import date
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from extant.agent.examples import ExampleCache, ExampleCacheError
from extant.agent.index import PgIndex
from extant.agent.llm import StructuredModel
from extant.agent.service import Readiness, Service, readiness
from extant.core.schema import in_build
from extant.guards import logs
from extant.guards.middleware import CallRecord, PgUsageStore, RequestRecord, UsageStore
from extant.index.embed import Embedder

CONNECT_TIMEOUT_SECONDS = 4  # one attempt at a new server connection
POOL_TIMEOUT_SECONDS = 4.0  # waiting for a pooled connection before it counts as an outage
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 4  # one container: a handful of concurrent requests, each brief on the database
POOL_MAX_IDLE_SECONDS = 300.0

Models = Callable[[UsageStore], Mapping[str, StructuredModel]]


class NotReadyError(RuntimeError):
    """The configured build may not be served (see ``extant.agent.service.readiness``)."""


def make_pool(
    dsn: str,
    *,
    min_size: int = POOL_MIN_SIZE,
    max_size: int = POOL_MAX_SIZE,
    timeout: float = POOL_TIMEOUT_SECONDS,
    connect_timeout: int = CONNECT_TIMEOUT_SECONDS,
) -> ConnectionPool:
    """A pool that opens in the background: a database that is down at start leaves the
    process up (to answer liveness and serve cached examples), not crashed."""
    pool = ConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        max_idle=POOL_MAX_IDLE_SECONDS,
        kwargs={
            "autocommit": True,
            "prepare_threshold": None,
            "connect_timeout": connect_timeout,
        },
        check=ConnectionPool.check_connection,
        open=False,
        name="extant-serving",
    )
    pool.open(wait=False)
    return pool


class Serving:
    def __init__(
        self,
        *,
        pool: ConnectionPool,
        schema: str,
        cap: int,
        embedder: Embedder,
        models: Models,
        eur_per_usd: float,
        cache: ExampleCache | None,
    ) -> None:
        self.pool = pool
        self.schema = schema
        self.cap = cap
        self.embedder = embedder
        self.models = models
        self.eur_per_usd = eur_per_usd
        self.cache = cache
        self._entities: dict[str, str] | None = None
        self._lock = threading.Lock()

    def entities(self) -> Mapping[str, str]:
        """The build's entity labels, read once. Raises a database error if unreachable."""
        with self._lock:
            if self._entities is None:
                with connection(self) as conn:
                    rows = conn.execute("SELECT code, label FROM entity_types ORDER BY code")
                    self._entities = {str(c): str(label) for c, label in rows.fetchall()}
            return self._entities

    def readiness(self) -> Readiness:
        """The manifest check, over a pooled connection: a successful query proves the
        database too. Raises a database error if it cannot be reached."""
        with self.pool.connection() as conn:
            return readiness(conn, self.schema, self.embedder.identity.model_id, self.cache)

    def close(self) -> None:
        self.pool.close()


def start(*, require_ready: bool = True) -> Serving:
    """Load the settings, open the pool, and with ``require_ready`` refuse a build that may
    not be served (the MCP server). The HTTP API starts regardless and reports readiness on
    its own probe, so a database that is down at start never makes the process crash-loop."""
    from extant.agent.settings import load_agent_settings
    from extant.core.settings import load_settings
    from extant.index.settings import load_index_settings

    settings = load_settings()
    agent = load_agent_settings()
    try:
        cache: ExampleCache | None = ExampleCache.load()
    except ExampleCacheError:
        cache = None
    serving = Serving(
        pool=make_pool(settings.reader_dsn.get_secret_value()),
        schema=settings.build_schema,
        cap=settings.daily_answer_cap,
        embedder=load_index_settings().embedder(),
        models=agent.models,
        eur_per_usd=agent.eur_per_usd,
        cache=cache,
    )
    if require_ready:
        try:
            ready = serving.readiness()
        except psycopg.Error as exc:
            serving.close()
            raise NotReadyError(f"the database cannot be reached: {type(exc).__name__}") from exc
        if not ready.ready:
            serving.close()
            raise NotReadyError("; ".join(ready.blocking))
    return serving


@contextlib.contextmanager
def connection(serving: Serving) -> Iterator[psycopg.Connection]:
    """A pooled connection with the build named, for one unit of work (one transaction)."""
    with serving.pool.connection() as conn, in_build(conn, serving.schema):
        yield conn


class _LazyStore:
    """``PgUsageStore`` on a pooled connection taken at first use. Its tables are in public
    and named so, and each write is its own transaction: no search path is needed."""

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


class _LazyEntities(Mapping[str, str]):
    """The labels, read when the graph first needs them: by then the database has answered
    the cap check, and a database that is down never delays the cached examples."""

    def __init__(self, serving: Serving) -> None:
        self._serving = serving

    def __getitem__(self, key: str) -> str:
        return self._serving.entities()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._serving.entities())

    def __len__(self) -> int:
        return len(self._serving.entities())


def store(serving: Serving, stack: contextlib.ExitStack) -> UsageStore:
    """The cap's counter on a pooled connection held until ``stack`` closes."""
    return _LazyStore(lambda: stack.enter_context(serving.pool.connection()), serving.cap)


@contextlib.contextmanager
def service(
    serving: Serving, *, log: Callable[[Mapping[str, Any]], None] = logs.emit
) -> Iterator[Service]:
    """A Service for one request: the models behind the middleware, the daily cap counted in
    Postgres, a pooled index connection per attempt, every connection returned at the end."""
    with contextlib.ExitStack() as stack:
        counter = store(serving, stack)
        yield Service(
            index_version=serving.schema,
            open_index=lambda: PgIndex(
                stack.enter_context(serving.pool.connection()), serving.schema, serving.embedder
            ),
            models=serving.models(counter),
            store=counter,
            entities=_LazyEntities(serving),
            cache=serving.cache,
            eur_per_usd=serving.eur_per_usd,
            log=log,
        )
