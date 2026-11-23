"""The middleware: four guards around every model call.

A guard placed in a node protects the calls that node makes. These wrap the model object
itself, so they bind a call added six months from now by someone who never read this file:
the only way to get a model in this code base is ``AgentSettings.models()``, which hands out
``GuardedModel``s, and a boundary test holds that nothing else constructs one.

In the order they run on each call:

1. **The daily cap.** Before anything is sent, today's answered count is read from Postgres;
   at or over the cap the call is refused with ``CapReachedError``. If the counter cannot be read
   the call is refused too: the cap binds every call or it binds nothing, so it fails closed.
2. **Redaction.** The system prompt and the user message go through the same redactor as
   admission. Admission already redacted the question; this is the backstop for a prompt
   built from something else.
3. **Retry and backoff.** One policy, one place to tune: a transient failure (rate limit,
   overload, timeout, connection, 5xx) is retried once after a backoff, then raised as
   ``ModelUnavailableError``, which the graph turns into the retrieval-only rung. The provider
   client's own retries are switched off, so there is exactly one retry policy.
4. **Accounting.** Every completed call is counted in Postgres (model calls and euros for the
   day) and in the request's ledger, whether or not the caller logs its own cost.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, TypeVar
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel

from extant.agent.llm import Completion, StructuredModel
from extant.agent.pricing import chat_eur
from extant.guards import pii
from extant.guards.errors import CapReachedError, ModelUnavailableError

__all__ = ["CapReachedError", "ModelUnavailableError"]

T = TypeVar("T", bound=BaseModel)

INDIA = ZoneInfo("Asia/Kolkata")


def today_in_india(now: Callable[[], datetime] | None = None) -> date:
    return (now() if now else datetime.now(INDIA)).astimezone(INDIA).date()


# --------------------------------------------------------------------------- the counter


@dataclass(frozen=True)
class CallRecord:
    model: str
    input_tokens: int
    output_tokens: int
    eur: float
    latency_ms: float
    attempts: int
    redacted: tuple[str, ...]  # kinds the backstop removed from this prompt; normally none


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    day: date
    index_version: str
    query_sha256: str
    outcome: str
    refusal_reason: str | None
    model_calls: int
    eur: float


class UsageStore(Protocol):
    """Where the cap counter lives. ``PgUsageStore`` in serving; ``MemoryUsageStore`` in
    tests and fixture recording."""

    cap: int

    def answered(self, day: date) -> int: ...

    def record_call(self, day: date, call: CallRecord) -> None: ...

    def record_request(self, request: RequestRecord) -> None: ...


@dataclass
class MemoryUsageStore:
    cap: int = 50
    days: dict[date, dict[str, float]] = field(default_factory=dict)
    calls: list[CallRecord] = field(default_factory=list)
    requests: list[RequestRecord] = field(default_factory=list)

    def _day(self, day: date) -> dict[str, float]:
        return self.days.setdefault(
            day, {"requests": 0, "answered": 0, "model_calls": 0, "model_eur": 0.0}
        )

    def answered(self, day: date) -> int:
        return int(self._day(day)["answered"])

    def record_call(self, day: date, call: CallRecord) -> None:
        d = self._day(day)
        d["model_calls"] += 1
        d["model_eur"] += call.eur
        self.calls.append(call)

    def record_request(self, request: RequestRecord) -> None:
        d = self._day(request.day)
        d["requests"] += 1
        d["answered"] += int(request.outcome == "answer")
        self.requests.append(request)


class PgUsageStore:
    """The counter in ``public.usage_daily``, the ledger in ``public.request_ledger``.

    A request's ledger row and the counter it moves are written in one transaction, so a
    crash between them cannot count an answer twice or not at all. Concurrent requests may
    each see 49 and each answer, so the cap can be exceeded by at most the number of
    requests in flight; the counter never under-counts."""

    def __init__(self, conn: psycopg.Connection, cap: int) -> None:
        self.conn = conn
        self.cap = cap

    def answered(self, day: date) -> int:
        row = self.conn.execute(
            "SELECT answered FROM public.usage_daily WHERE day = %s", (day,)
        ).fetchone()
        return int(row[0]) if row else 0

    def record_call(self, day: date, call: CallRecord) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO public.usage_daily (day, model_calls, model_eur) VALUES (%s, 1, %s)"
                " ON CONFLICT (day) DO UPDATE SET model_calls = usage_daily.model_calls + 1,"
                " model_eur = usage_daily.model_eur + EXCLUDED.model_eur",
                (day, round(call.eur, 6)),
            )

    def record_request(self, request: RequestRecord) -> None:
        answered = int(request.outcome == "answer")
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO public.request_ledger (request_id, day, index_version,"
                " query_sha256, outcome, refusal_reason, model_calls, eur)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    request.request_id,
                    request.day,
                    request.index_version,
                    request.query_sha256,
                    request.outcome,
                    request.refusal_reason,
                    request.model_calls,
                    round(request.eur, 6),
                ),
            )
            self.conn.execute(
                "INSERT INTO public.usage_daily (day, requests, answered) VALUES (%s, 1, %s)"
                " ON CONFLICT (day) DO UPDATE SET requests = usage_daily.requests + 1,"
                " answered = usage_daily.answered + EXCLUDED.answered",
                (request.day, answered),
            )


# --------------------------------------------------------------------------- the ledger


@dataclass
class Ledger:
    """The model calls of one request, collected whoever made them."""

    calls: list[CallRecord] = field(default_factory=list)

    @property
    def eur(self) -> float:
        return sum(c.eur for c in self.calls)


_LEDGER: contextvars.ContextVar[Ledger | None] = contextvars.ContextVar("ledger", default=None)


@contextmanager
def ledger() -> Iterator[Ledger]:
    """Collect every guarded call made inside the block into one ledger."""
    led = Ledger()
    token = _LEDGER.set(led)
    try:
        yield led
    finally:
        _LEDGER.reset(token)


# --------------------------------------------------------------------------- retry


_TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_TRANSIENT_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "OverloadedError",
        "ServiceUnavailableError",
        "ConnectError",
        "ReadTimeout",
        "ConnectTimeout",
        "RemoteProtocolError",
    }
)


def transient(exc: BaseException) -> bool:
    """A failure worth one retry: the provider was busy or unreachable, not wrong."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if getattr(exc, "status_code", None) in _TRANSIENT_STATUS:
        return True
    return any(t.__name__ in _TRANSIENT_NAMES for t in type(exc).__mro__)


@dataclass(frozen=True)
class RetryPolicy:
    retries: int = 1
    backoff_s: float = 2.0
    sleep: Callable[[float], None] = time.sleep


# --------------------------------------------------------------------------- the guard


class GuardedModel:
    def __init__(
        self,
        inner: StructuredModel,
        *,
        store: UsageStore,
        eur_per_usd: float,
        retry: RetryPolicy | None = None,
        day: Callable[[], date] = today_in_india,
    ) -> None:
        self.inner = inner
        self.store = store
        self.eur_per_usd = eur_per_usd
        self.retry = retry or RetryPolicy()
        self.day = day

    @property
    def model(self) -> str:
        return self.inner.model

    def complete(self, schema: type[T], *, system: str, user: str) -> Completion:
        day = self.day()
        answered = self.store.answered(day)  # a counter that cannot be read refuses the call
        if answered >= self.store.cap:
            raise CapReachedError(f"{answered} of {self.store.cap} questions answered on {day}")
        clean_system, clean_user = pii.redact(system), pii.redact(user)
        attempts = 0
        while True:
            attempts += 1
            try:
                done = self.inner.complete(schema, system=clean_system.text, user=clean_user.text)
                break
            except Exception as exc:
                if not transient(exc):
                    raise
                if attempts > self.retry.retries:
                    raise ModelUnavailableError(
                        f"{self.model} unavailable after {attempts} attempts: {exc}"
                    ) from exc
                self.retry.sleep(self.retry.backoff_s * attempts)
        call = CallRecord(
            model=done.model,
            input_tokens=done.input_tokens,
            output_tokens=done.output_tokens,
            eur=chat_eur(done.model, done.input_tokens, done.output_tokens, self.eur_per_usd),
            latency_ms=done.latency_ms,
            attempts=attempts,
            redacted=tuple(dict.fromkeys([*clean_system.kinds, *clean_user.kinds])),
        )
        self.store.record_call(day, call)
        current = _LEDGER.get()
        if current is not None:
            current.calls.append(call)
        return done


def guard_all(
    models: dict[str, StructuredModel], store: UsageStore, eur_per_usd: float, **kw: Any
) -> dict[str, StructuredModel]:
    return {
        name: GuardedModel(m, store=store, eur_per_usd=eur_per_usd, **kw)
        for name, m in models.items()
    }
