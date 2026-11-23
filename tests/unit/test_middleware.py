"""The middleware binds every model call: redaction, accounting, the cap and one retry.

The strongest claim is the first test's: a model call written outside the graph, by code
that knows nothing about guards, is still redacted, counted and capped, because the only way
to obtain a model is ``AgentSettings.models()``.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

import psycopg
import pytest
from pydantic import SecretStr

from extant.agent.llm import Completion, FakeModel
from extant.agent.models import JudgeOutput
from extant.agent.pricing import UnpricedModelError
from extant.agent.settings import AgentSettings
from extant.guards.errors import CapReachedError, ModelUnavailableError
from extant.guards.middleware import (
    GuardedModel,
    MemoryUsageStore,
    RetryPolicy,
    ledger,
    transient,
)

DAY = date(2026, 11, 22)
REPO = Path(__file__).resolve().parents[2]


class _Recorder:
    """Stands in for LangChainModel: records what would have reached the provider."""

    seen: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, tier: Any, api_key: SecretStr) -> None:
        self._model = tier.model

    @property
    def model(self) -> str:
        return self._model

    def complete(self, schema: Any, *, system: str, user: str) -> Completion:
        _Recorder.seen.append((system, user))
        return Completion(JudgeOutput(supported=True), self._model, 100, 5, 1.0)


def test_a_throwaway_call_outside_the_graph_is_redacted_counted_and_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("extant.agent.settings.LangChainModel", _Recorder)
    _Recorder.seen = []
    store = MemoryUsageStore(cap=2)
    models = AgentSettings(anthropic_api_key=SecretStr("test")).models(store)
    model = models["judge"]
    guarded = model if isinstance(model, GuardedModel) else None
    assert guarded is not None
    guarded.day = lambda: DAY

    # Someone's new feature, six months on: a direct call, no graph, no node, no Spend.
    with ledger() as led:
        model.complete(JudgeOutput, system="classify", user="My PAN is ABCPE1234F. Supported?")
    assert _Recorder.seen == [("classify", "My PAN is <PAN>. Supported?")]  # redacted
    assert store.days[DAY]["model_calls"] == 1  # counted in the daily counter
    assert len(led.calls) == 1
    assert led.calls[0].eur > 0

    store.days[DAY]["answered"] = 2  # the cap is answered questions, not calls
    with pytest.raises(CapReachedError):
        model.complete(JudgeOutput, system="classify", user="anything")
    assert len(_Recorder.seen) == 1  # capped before anything was sent


def _fake(**kw: Any) -> FakeModel:
    return FakeModel(lambda s, sy, u: JudgeOutput(supported=True), name="claude-haiku-5-5", **kw)


class _Flaky:
    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        self.calls = 0

    @property
    def model(self) -> str:
        return "claude-haiku-5-5"

    def complete(self, schema: Any, *, system: str, user: str) -> Completion:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return Completion(JudgeOutput(supported=True), self.model, 10, 1, 1.0)


class RateLimitError(Exception):  # named like the provider's, which is how it is recognised
    status_code = 429


def _guard(inner: Any, store: MemoryUsageStore | None = None) -> tuple[GuardedModel, list[float]]:
    slept: list[float] = []
    g = GuardedModel(
        inner,
        store=store or MemoryUsageStore(),
        eur_per_usd=0.9,
        retry=RetryPolicy(retries=1, backoff_s=2.0, sleep=slept.append),
        day=lambda: DAY,
    )
    return g, slept


def test_a_transient_failure_is_retried_once_after_a_backoff() -> None:
    inner = _Flaky([RateLimitError("slow down")])
    g, slept = _guard(inner)
    g.complete(JudgeOutput, system="s", user="u")
    assert (inner.calls, slept) == (2, [2.0])


def test_a_second_transient_failure_is_model_unavailable() -> None:
    inner = _Flaky([TimeoutError(), RateLimitError("still")])
    g, slept = _guard(inner)
    with pytest.raises(ModelUnavailableError):
        g.complete(JudgeOutput, system="s", user="u")
    assert inner.calls == 2
    assert slept == [2.0]


def test_a_failure_that_is_not_transient_is_not_retried() -> None:
    inner = _Flaky([ValueError("bad schema")])
    g, slept = _guard(inner)
    with pytest.raises(ValueError, match="bad schema"):
        g.complete(JudgeOutput, system="s", user="u")
    assert inner.calls == 1
    assert slept == []


def test_transient_is_recognised_by_status_or_name() -> None:
    assert transient(RateLimitError())
    assert transient(TimeoutError())
    assert transient(type("OverloadedError", (Exception,), {})())
    assert not transient(ValueError())


def test_an_unpriced_model_cannot_be_called_unaccounted() -> None:
    g, _ = _guard(FakeModel(lambda s, sy, u: JudgeOutput(supported=True), name="mystery-model"))
    with pytest.raises(UnpricedModelError):
        g.complete(JudgeOutput, system="s", user="u")


def test_the_cap_fails_closed_when_the_counter_cannot_be_read() -> None:
    class Down(MemoryUsageStore):
        def answered(self, day: date) -> int:
            raise psycopg.OperationalError("connection refused")

    inner = _fake()
    g, _ = _guard(inner, Down())
    with pytest.raises(psycopg.OperationalError):
        g.complete(JudgeOutput, system="s", user="u")
    assert inner.calls == []


def test_nothing_but_the_settings_factory_builds_a_model_client() -> None:
    # init_chat_model appears in one module, LangChainModel is constructed in one place, and
    # that place wraps it: so every model in the code base is a guarded one.
    sources = {p: p.read_text(encoding="utf-8") for p in (REPO / "extant").rglob("*.py")}
    chat = {p.relative_to(REPO).as_posix() for p, s in sources.items() if "init_chat_model" in s}
    assert chat == {"extant/agent/llm.py"}
    built = {
        p.relative_to(REPO).as_posix()
        for p, s in sources.items()
        if re.search(r"\bLangChainModel\(", s) and "class LangChainModel" not in s
    }
    assert built == {"extant/agent/settings.py"}
    imports_provider = {
        p.relative_to(REPO).as_posix()
        for p, s in sources.items()
        if re.search(r"^\s*(?:from|import) (?:anthropic|openai|langchain_anthropic)\b", s, re.M)
    }
    assert imports_provider == set()
