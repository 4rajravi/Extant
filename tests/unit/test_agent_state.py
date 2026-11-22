"""The state validators: the bad states the design makes unrepresentable."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from extant.agent.models import (
    AgentState,
    Claim,
    DraftClaim,
    RefusalReason,
)
from extant.agent.nodes.verify import run as verify
from extant.agent.runtime import Given, Spend
from tests.agent_support import TODAY, cand


def state(**kw: object) -> AgentState:
    base: dict[str, object] = {
        "request_id": "r",
        "index_version": "idx_rbi_20261120_38ede3c",
        "raw_query": "q",
        "today": TODAY,
        "as_of": TODAY,
    }
    return AgentState(**{**base, **kw})  # type: ignore[arg-type]


def test_a_citation_outside_the_candidates_is_unrepresentable() -> None:
    claim = DraftClaim(text="x", citation_ids=("rbi:md:2025:0009:v1#p1",), quotes=("x",))
    with pytest.raises(ValidationError, match="not candidates"):
        state(candidates=[cand(1)], draft_claims=[claim])


def test_an_answer_with_no_verified_claims_is_unrepresentable() -> None:
    with pytest.raises(ValidationError, match="answer with no verified claims"):
        state(outcome="answer")


def test_a_refusal_needs_a_reason_and_an_answer_has_none() -> None:
    with pytest.raises(ValidationError, match="needs a reason"):
        state(outcome="refusal")
    good = verify(
        state(
            candidates=[cand(1)],
            draft_claims=[
                DraftClaim(
                    text="A bank shall maintain a minimum capital ratio of 9 per cent.",
                    citation_ids=(cand(1).para_id,),
                    quotes=("minimum capital ratio of 9 per cent",),
                )
            ],
        ),
        Given(),
        Spend(),
    )
    claims: list[Claim] = good["verified_claims"]
    with pytest.raises(ValidationError, match="no refusal reason"):
        state(
            outcome="answer", verified_claims=claims, refusal_reason=RefusalReason.NO_SUPPORT_FOUND
        )


def test_an_expanded_row_never_carries_a_fusion_score() -> None:
    row = cand(1, origin="expanded")
    assert row.fusion_score is None
    assert row.rank is None
    with pytest.raises(ValidationError, match="no score or rank"):
        type(row).model_validate({**row.model_dump(), "fusion_score": 0.1})


def test_the_graph_cannot_refuse_for_the_cap() -> None:
    with pytest.raises(ValidationError, match="API layer"):
        state(refusal_reason=RefusalReason.CAP_REACHED)


def test_claims_over_a_paragraph_out_of_force_are_unrepresentable() -> None:
    stale = cand(1, valid_from=date(2024, 1, 1), valid_to=date(2025, 1, 1))
    claim = DraftClaim(text="x", citation_ids=(stale.para_id,), quotes=("A bank",))
    with pytest.raises(ValidationError, match="not in force"):
        state(candidates=[stale], draft_claims=[claim])


def test_composer_and_state_claims_are_different_objects() -> None:
    from extant.agent.models import ComposerClaim

    assert set(ComposerClaim.model_fields) & {"citation_ids"} == set()
    assert set(DraftClaim.model_fields) & {"candidate_indices"} == set()
