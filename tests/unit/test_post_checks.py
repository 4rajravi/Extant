"""The post checks run from two places: verify refuses, finalize raises.

A state that reaches finalize with a failing check means a path bypassed verification. That
is a bug, so it must raise rather than become a user facing refusal.
"""

from __future__ import annotations

from typing import Any

import pytest

from extant.agent import graph
from extant.agent.models import AgentState, RefusalReason
from extant.agent.nodes import finalize, verify
from extant.agent.runtime import Given, Spend
from extant.guards import post
from extant.guards.post import PostCheckError
from tests.agent_support import (
    ScriptedIndex,
    cand,
    composer_says,
    judge_says,
    parse_says,
    run_graph,
)

BODY = "A bank shall maintain a minimum capital ratio of 9 per cent of its risk weighted assets."
GOOD = (
    "A bank must keep a minimum capital ratio of 9 per cent.",
    [0],
    ["minimum capital ratio of 9 per cent"],
)
BAD = ("A bank must keep a minimum capital ratio of 9 per cent.", [0], ["words the body lacks"])


def _ask(claim: tuple[str, list[int], list[str]], **kw: Any) -> AgentState:
    return run_graph(
        "What capital ratio must a bank keep?",
        index=ScriptedIndex(ranked=[cand(1, BODY)]),
        parse=parse_says(),
        compose=composer_says(claim),
        judge=kw.pop("judge", judge_says()),
        **kw,
    )


def test_verify_refuses_a_bad_quote_with_the_check_named() -> None:
    state = _ask(BAD)
    assert state.refusal_reason is RefusalReason.VERIFICATION_FAILED
    assert state.refusal_detail == "a quote"


def test_a_path_that_bypasses_verify_raises_in_finalize(monkeypatch: pytest.MonkeyPatch) -> None:
    real = verify.run

    def lenient(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
        # A verify that checked something other than what the state holds.
        fixed = [c.model_copy(update={"quotes": tuple(GOOD[2])}) for c in state.draft_claims]
        return real(state.model_copy(update={"draft_claims": fixed}), given, spend)

    monkeypatch.setitem(graph._FNS, "verify", lenient)
    with pytest.raises(PostCheckError, match="quote_match"):
        _ask(BAD)


def test_finalize_raises_on_a_residual_claim_with_no_judge_pass() -> None:
    # Overlap below the threshold, judged supported: an answer. Remove the judge's verdict
    # from the record and the same final state fails its post check.
    claim = ("Banks must hold capital.", [0], ["minimum capital ratio of 9 per cent"])
    state = _ask(claim)
    assert state.outcome == "answer"
    assert state.residual == [0]
    stripped = state.model_copy(
        update={
            "checks": {k: v for k, v in state.checks.items() if not k.endswith(".judge")},
            "outcome": None,
            "response": None,
        }
    )
    with pytest.raises(PostCheckError, match="groundedness"):
        finalize.run(stripped, Given(), Spend())


def test_the_checks_pass_on_every_answer_the_graph_gives() -> None:
    state = _ask(GOOD)
    assert state.outcome == "answer"
    assert all(r.passed for r in post.answer_checks(state))


def test_an_answer_citing_only_non_binding_material_fails_the_authority_check() -> None:
    state = _ask(GOOD)
    faq = [c.model_copy(update={"binding": False, "authority_rank": 60}) for c in state.candidates]
    results = post.answer_checks(state.model_copy(update={"candidates": faq}))
    failed = {r.name for r in results if not r.passed}
    # The citations change with the candidates, so the verified claims no longer match either.
    assert failed == {"authority", "verified_claims"}
