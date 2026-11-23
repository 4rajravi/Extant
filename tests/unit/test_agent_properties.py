"""The two properties that carry the weight, generated rather than enumerated.

1. Nothing out of force on its date survives the gate, and every removal is in ``dropped``
   with the right reason.
2. Any broken citation, unmatched quote, failed numeric check or failed groundedness check
   forces ``verification_failed``, and never an answer.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from extant.agent.checks import verify_claim
from extant.agent.models import AgentState, Candidate, DraftClaim, RefusalReason
from extant.agent.nodes import gate
from extant.agent.runtime import Given, Spend
from tests.agent_support import (
    TODAY,
    ScriptedIndex,
    cand,
    composer_says,
    judge_says,
    parse_says,
    run_graph,
)

CODES = ("cb", "ucb", "sfb")
days = st.integers(min_value=0, max_value=3000).map(lambda n: date(2020, 1, 1) + timedelta(n))


@st.composite
def candidates(draw: st.DrawFn) -> list[Candidate]:
    rows = []
    for i in range(draw(st.integers(min_value=0, max_value=12))):
        start = draw(days)
        end = draw(
            st.none()
            | st.integers(min_value=1, max_value=900).map(lambda n, s=start: s + timedelta(n))
        )
        scope = tuple(sorted(draw(st.sets(st.sampled_from(CODES), min_size=1))))
        asked = draw(days)
        rows.append(
            cand(
                i + 1,
                valid_from=start,
                valid_to=end,
                scope=scope,
                asked_for=asked,
                doc=f"rbi:md:2025:{i + 1:04d}:v1",
            )
        )
    return rows


@settings(max_examples=400, deadline=None)
@given(rows=candidates(), entity=st.none() | st.sampled_from(CODES), on=days)
def test_nothing_out_of_force_survives_the_gate_and_every_removal_is_recorded(
    rows: list[Candidate], entity: str | None, on: date
) -> None:
    s = AgentState(
        request_id="r",
        index_version="idx_rbi_20261120_38ede3c",
        raw_query="q",
        today=TODAY,
        as_of=on,
        entity_type=entity,
        candidates=rows,
    )
    out = gate.run(s, Given(), Spend())
    kept = {c.para_id for c in out["candidates"]}
    dropped = {d.para_id: d for d in out["dropped"]}
    # One dropped row per removed candidate, exactly: the dict above would hide a duplicate.
    assert len(out["dropped"]) == len(rows) - len(out["candidates"])
    assert kept.isdisjoint(dropped)
    assert kept | set(dropped) == {c.para_id for c in rows}
    for c in rows:
        live = c.valid_from <= c.asked_for and (c.valid_to is None or c.valid_to > c.asked_for)
        scoped = entity is None or entity in c.entity_scope
        if c.para_id in kept:
            assert live
            assert scoped
        else:
            reason = dropped[c.para_id].reason
            if not live:
                assert reason in ("superseded", "not_yet_effective")
            else:
                assert reason == "out_of_entity_scope"
    assert out["set_aside"] == [c for c in rows if c.para_id in dropped]
    if not kept:
        assert out["refusal_reason"] in (
            RefusalReason.SUPERSEDED_ONLY,
            RefusalReason.NO_SUPPORT_FOUND,
        )


@settings(max_examples=400, deadline=None)
@given(rows=candidates(), on=days)
def test_repealed_never_reaches_compose(rows: list[Candidate], on: date) -> None:
    """The Phase 6 spec's form: every candidate retrieved for the state's own as-of date."""
    rows = [c.model_copy(update={"asked_for": on}) for c in rows]
    state = AgentState(
        request_id="r",
        index_version="idx_rbi_20261123_c23c1e8",
        raw_query="q",
        today=TODAY,
        as_of=on,
        candidates=rows,
    )
    out = gate.run(state, Given(), Spend())
    for c in out["candidates"]:
        assert c.valid_from <= on
        assert c.valid_to is None or c.valid_to > on
    assert len(out["dropped"]) == len(state.candidates) - len(out["candidates"])


# --------------------------------------------------------------------------- planted defects

BODY = "A bank shall maintain a minimum capital ratio of 9 per cent of its risk weighted assets."
GOOD = (
    "A bank must keep a minimum capital ratio of 9 per cent.",
    "minimum capital ratio of 9 per cent",
)
DEFECTS = ("quote", "numeric", "index", "groundedness")


def _defective(kind: str, n: int) -> tuple[str, list[int], list[str]]:
    text, quote = GOOD
    if kind == "quote":
        return text, [0], [quote.replace("9 per cent", "9 percent") + " " * (n % 3)]
    if kind == "numeric":
        return text.replace("9 per cent", f"{10 + n} per cent"), [0], [quote]
    if kind == "index":
        return text, [1 + n], [quote]
    # groundedness: every word quoted correctly, but the claim says something else
    return "Lenders may ignore every ratio entirely.", [0], ["of its risk weighted assets"]


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    planted=st.lists(st.sampled_from(DEFECTS), min_size=1, max_size=3),
    good_claims=st.integers(min_value=0, max_value=2),
    n=st.integers(min_value=0, max_value=40),
)
def test_any_planted_defect_forces_verification_failed_and_never_an_answer(
    planted: list[str], good_claims: int, n: int
) -> None:
    claims = [(GOOD[0], [0], [GOOD[1]])] * good_claims + [_defective(k, n) for k in planted]
    state = run_graph(
        "What capital ratio must a bank keep?",
        index=ScriptedIndex(ranked=[cand(1, BODY)]),
        parse=parse_says(),
        compose=composer_says(*claims),
        judge=judge_says(supported=False),
    )
    assert state.outcome == "refusal"
    assert state.refusal_reason is RefusalReason.VERIFICATION_FAILED
    assert state.response is not None
    assert state.response.result.outcome == "refusal"


@settings(max_examples=200, deadline=None)
@given(missing=st.text(alphabet="abcdefghij:#", min_size=1, max_size=12))
def test_a_citation_that_is_not_a_candidate_never_verifies(missing: str) -> None:
    claim = DraftClaim(
        text=GOOD[0], citation_ids=(f"rbi:md:2025:0999:v1#{missing}",), quotes=(GOOD[1],)
    )
    verdict = verify_claim(0, claim, [cand(1, BODY)])
    assert not verdict.passed
    assert verdict.checks["citation_exists"].passed is False
    assert verdict.claim is None
