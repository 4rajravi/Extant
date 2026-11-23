"""The service: admission, the cap, and every rung of the degradation ladder.

Each rung is reached by faulting the dependency it exists for, and each marks the response's
``degraded`` list, so a user reading a thinner answer can see that it is thinner.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any

import psycopg
import pytest
from pydantic import ValidationError

from extant.agent.examples import STATIC_PAGE, ExampleCache
from extant.agent.models import (
    AgentState,
    Answer,
    CachedExample,
    Caveat,
    Refusal,
    RefusalReason,
    RetrievalOnly,
    Unavailable,
)
from extant.agent.runtime import Index
from extant.agent.service import Service
from extant.guards.middleware import MemoryUsageStore, guard_all
from extant.guards.pii import found
from tests.agent_support import (
    ENTITIES,
    TODAY,
    ScriptedIndex,
    cand,
    composer_says,
    judge_says,
    parse_says,
)
from tests.replay_support import Down

BODY = "A bank shall maintain a minimum capital ratio of 9 per cent of its risk weighted assets."
CLAIM = (
    "A bank must keep a minimum capital ratio of 9 per cent.",
    [0],
    ["minimum capital ratio of 9 per cent"],
)
QUESTION = "What capital ratio must a bank keep?"
VERSION = "idx_rbi_20261123_c23c1e8"


def service(
    *,
    models: Mapping[str, Any] | None = None,
    store: MemoryUsageStore | None = None,
    cache: ExampleCache | None = None,
    open_index: Any = None,
    logs: list[Mapping[str, Any]] | None = None,
) -> Service:
    index = ScriptedIndex(ranked=[cand(1, BODY), cand(2, BODY, doc="rbi:md:2025:0002:v1")])
    return Service(
        index_version=VERSION,
        open_index=open_index or (lambda: index),
        models=dict(
            models
            or {"parse": parse_says(), "compose": composer_says(CLAIM), "judge": judge_says()}
        ),
        store=store or MemoryUsageStore(),
        entities=ENTITIES,
        cache=cache,
        log=logs.append if logs is not None else (lambda r: None),
        day=lambda: TODAY,
    )


def a_cache(version: str = VERSION) -> ExampleCache:
    answer = service().ask(QUESTION, entity="cb").result
    assert isinstance(answer, Answer)
    example = CachedExample(
        question=QUESTION, entity_type="cb", as_of=TODAY, index_version=version, answer=answer
    )
    return ExampleCache(version, TODAY, (example,))


def at_cap(cap: int = 50) -> MemoryUsageStore:
    store = MemoryUsageStore(cap=cap)
    store.days[TODAY] = {"requests": cap, "answered": cap, "model_calls": 0, "model_eur": 0.0}
    return store


# --------------------------------------------------------------------------- admission


def test_the_question_is_redacted_before_anything_sees_it() -> None:
    parse, compose = parse_says(), composer_says(CLAIM)
    logs: list[Mapping[str, Any]] = []
    svc = service(models={"parse": parse, "compose": compose, "judge": judge_says()}, logs=logs)
    r = svc.ask("My PAN is ABCPE1234F, mobile 9876543210: " + QUESTION, entity="cb")
    assert "<PAN>" in parse.calls[0][2]
    assert "ABCPE1234F" not in parse.calls[0][2]
    assert "<MOBILE>" in compose.calls[0][2]
    assert "9876543210" not in compose.calls[0][2]
    assert r.redactions == ["PAN", "MOBILE"]
    assert r.notice is not None
    assert "a PAN and a mobile number" in r.notice
    assert "<PAN>" in logs[0]["query_redacted"]


def test_the_state_refuses_a_question_that_skipped_admission() -> None:
    with pytest.raises(ValidationError, match="unredacted identifier"):
        AgentState(request_id="r", index_version=VERSION, raw_query="PAN ABCPE1234F", today=TODAY)


def test_no_log_record_carries_an_unredacted_identifier() -> None:
    logs: list[Mapping[str, Any]] = []
    questions = [
        "My PAN is ABCPE1234F: " + QUESTION,
        "Aadhaar 2345 6789 0123 and a/c 50100123456789: " + QUESTION,
        "Calculate my CRAR; call me on +91 98765 43210",
        "GST for GSTIN 27ABCPE1234F1Z5 at SBIN0001234?",
        "Email x.y@example.com about circular DBOD.No.BP.BC.12/21.04.048/2014-15",
    ]
    for q in questions:
        service(logs=logs).ask(q, entity="cb")
    service(logs=logs, store=at_cap()).ask(questions[0], entity="cb")
    assert len(logs) == len(questions) + 1
    for record in logs:
        assert found(json.dumps(record, ensure_ascii=False)) == [], record


# --------------------------------------------------------------------------- the cap


def test_at_the_cap_only_cached_examples_are_served_and_no_model_is_called() -> None:
    parse = parse_says()
    store = at_cap()
    r = service(
        models={"parse": parse, "compose": composer_says(CLAIM), "judge": judge_says()},
        store=store,
        cache=a_cache(),
    ).ask(QUESTION, entity="cb")
    assert isinstance(r.result, Refusal)
    assert r.result.reason is RefusalReason.CAP_REACHED
    assert len(r.result.examples) == 1
    assert "50 questions" in r.result.message
    assert r.degraded == ["cached_examples"]
    assert parse.calls == []
    assert store.requests[-1].outcome == "refusal"


def test_the_cap_binds_a_model_call_mid_request() -> None:
    class Fills(MemoryUsageStore):
        """Under the cap at admission and for parse, at the cap by the time compose runs."""

        reads = 0

        def answered(self, day: date) -> int:
            self.reads += 1
            return self.cap if self.reads > 2 else 0

    store = Fills(cap=50)
    models = guard_all(
        {"parse": parse_says(), "compose": composer_says(CLAIM), "judge": judge_says()},
        store,
        0.9,
        day=lambda: TODAY,
    )
    r = service(models=models, store=store).ask(QUESTION, entity="cb")
    assert isinstance(r.result, Refusal)
    assert r.result.reason is RefusalReason.CAP_REACHED
    assert store.requests[-1].model_calls == 1  # parse ran, compose was refused


def test_the_counter_moves_on_answers_only_and_counts_model_calls_beside_them() -> None:
    store = MemoryUsageStore(cap=50)
    models = guard_all(
        {"parse": parse_says(), "compose": composer_says(CLAIM), "judge": judge_says()},
        store,
        0.9,
        day=lambda: TODAY,
    )
    svc = service(models=models, store=store)
    svc.ask(QUESTION, entity="cb")
    svc.ask("Calculate my CRAR for 500 crore of capital", entity="cb")
    day = store.days[TODAY]
    assert (day["requests"], day["answered"], day["model_calls"]) == (2, 1, 2)


# --------------------------------------------------------------------------- the ladder


def test_rung_3_the_composer_down_gives_the_provisions_with_no_prose() -> None:
    r = service(
        models={"parse": parse_says(), "compose": Down("claude-opus-5-5"), "judge": judge_says()}
    ).ask(QUESTION, entity="cb")
    assert isinstance(r.result, RetrievalOnly)
    assert [p.para_id for p in r.result.provisions][:1] == [cand(1, BODY).para_id]
    assert r.result.provisions[0].quote_end == len(BODY)  # quoted whole
    assert r.degraded == ["retrieval_only"]
    assert Caveat.DEGRADED_RETRIEVAL in r.result.caveats


def test_rung_3_the_cheap_model_down_at_parse_skips_the_composer() -> None:
    compose = composer_says(CLAIM)
    r = service(
        models={"parse": Down("claude-haiku-5-5"), "compose": compose, "judge": judge_says()}
    ).ask(QUESTION, entity="cb")
    assert isinstance(r.result, RetrievalOnly)
    assert compose.calls == []
    assert r.degraded == ["retrieval_only"]


def test_rung_3_the_judge_down_shows_the_provisions_rather_than_an_unjudged_claim() -> None:
    residual = ("Banks must hold capital.", [0], ["minimum capital ratio of 9 per cent"])
    r = service(
        models={
            "parse": parse_says(),
            "compose": composer_says(residual),
            "judge": Down("claude-haiku-5-5"),
        }
    ).ask(QUESTION, entity="cb")
    assert isinstance(r.result, RetrievalOnly)
    assert r.degraded == ["retrieval_only"]


def _no_database(opened: list[int]) -> Any:
    def open_index() -> Index:
        opened.append(1)
        raise psycopg.OperationalError("could not connect: the endpoint is waking up")

    return open_index


def test_rung_4_postgres_down_retries_once_then_serves_cached_examples() -> None:
    opened: list[int] = []
    r = service(open_index=_no_database(opened), cache=a_cache()).ask(QUESTION, entity="cb")
    assert len(opened) == 2  # one retry
    assert isinstance(r.result, Unavailable)
    assert len(r.result.examples) == 1
    assert r.degraded == ["cached_examples"]


def test_postgres_back_on_the_retry_answers_normally() -> None:
    index = ScriptedIndex(ranked=[cand(1, BODY)])
    opened: list[int] = []

    def flaky() -> Index:
        opened.append(1)
        if len(opened) == 1:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        return index

    r = service(open_index=flaky).ask(QUESTION, entity="cb")
    assert isinstance(r.result, Answer)
    assert len(opened) == 2


def test_rung_5_with_no_cache_the_static_page_is_served() -> None:
    r = service(open_index=_no_database([])).ask(QUESTION, entity="cb")
    assert isinstance(r.result, Unavailable)
    assert r.result.message == STATIC_PAGE
    assert r.result.examples == []
    assert r.degraded == ["static_page"]


def test_an_unreadable_counter_is_treated_as_postgres_down_and_calls_no_model() -> None:
    class Down(MemoryUsageStore):
        def answered(self, day: date) -> int:
            raise psycopg.OperationalError("connection refused")

    parse = parse_says()
    r = service(
        models={"parse": parse, "compose": composer_says(CLAIM), "judge": judge_says()},
        store=Down(),
        cache=a_cache(),
    ).ask(QUESTION, entity="cb")
    assert isinstance(r.result, Unavailable)
    assert parse.calls == []


def test_a_stale_cache_is_served_and_says_so() -> None:
    r = service(store=at_cap(), cache=a_cache("idx_rbi_20261120_38ede3c")).ask(
        QUESTION, entity="cb"
    )
    assert r.degraded == ["cached_examples", "stale_examples"]
