"""One test group per node: what it writes, and nothing it does not own."""

from __future__ import annotations

from datetime import date

import pytest

from extant.agent.llm import FakeModel
from extant.agent.models import (
    AgentState,
    Caveat,
    ComposerClaim,
    ComposerOutput,
    DraftClaim,
    ParseOutput,
    RefusalReason,
)
from extant.agent.nodes import (
    authority,
    compose,
    expand,
    finalize,
    gate,
    judge,
    parse,
    refuse_early,
    verify,
)
from extant.agent.runtime import WRITES, Deps, Given, InvariantError, Spend, run_node
from tests.agent_support import ENTITIES, TODAY, cand, composer_says, judge_says, parse_says


def state(**kw: object) -> AgentState:
    base: dict[str, object] = {
        "request_id": "r",
        "index_version": "idx_rbi_20261120_38ede3c",
        "raw_query": "What capital must a bank keep?",
        "today": TODAY,
    }
    return AgentState(**{**base, **kw})  # type: ignore[arg-type]


def given(model: FakeModel | None = None) -> Given:
    return Given(model=model, entities=ENTITIES)


# --------------------------------------------------------------------------- parse


@pytest.mark.parametrize(
    "question",
    [
        "What capital ratio must a commercial bank maintain?",
        "what is the minimum CRAR for banks",
        "Minimum capital requirements, please, for a bank.",
        "Tell me the capital adequacy norm that applies to a scheduled commercial bank",
    ],
)
def test_parse_writes_the_model_fields_over_phrasing_variants(question: str) -> None:
    model = parse_says(entity_type="cb", topic_terms=["capital", "CRAR"])
    out = parse.run(state(raw_query=question), given(model), Spend())
    assert set(out) <= WRITES["parse"]
    assert out["path"] == "full"
    assert out["query_kind"] == "applicability"
    assert out["entity_type"] == "cb"
    assert out["as_of"] == TODAY
    assert out["as_of_source"] == "today"
    assert Caveat.AS_OF_ASSUMED in out["caveats"]
    assert len(model.calls) == 1


def test_parse_never_takes_an_entity_code_the_index_does_not_know() -> None:
    out = parse.run(state(), given(parse_says(entity_type="nbfc_made_up")), Spend())
    assert out["entity_type"] is None


def test_what_the_caller_supplied_wins_over_what_the_question_says() -> None:
    model = parse_says(entity_type="ucb", as_of=date(2025, 3, 1))
    out = parse.run(
        state(submitted_entity="cb", submitted_as_of=date(2026, 1, 1)), given(model), Spend()
    )
    assert (out["entity_type"], out["as_of"], out["as_of_source"]) == (
        "cb",
        date(2026, 1, 1),
        "submitted",
    )
    assert Caveat.AS_OF_ASSUMED not in out["caveats"]
    from_question = parse.run(state(), given(model), Spend())
    assert (from_question["as_of"], from_question["as_of_source"]) == (date(2025, 3, 1), "question")


@pytest.mark.parametrize(
    ("question", "path"),
    [
        ("What is the GST rate on bank charges?", "out_of_scope"),
        ("Calculate the CRAR for my bank with Tier 1 of 500 crore", "refuse"),
        ("What replaced circular DBOD.No.BP.BC.12/21.04.048/2014-15?", "fast"),
    ],
)
def test_parse_settles_three_routes_with_no_model_call(question: str, path: str) -> None:
    model = parse_says()
    out = parse.run(state(raw_query=question), given(model), Spend())
    assert out["path"] == path
    assert model.calls == []


def test_should_i_phrasing_gets_the_not_legal_advice_caveat() -> None:
    out = parse.run(
        state(raw_query="Can I lend to a director's relative?"), given(parse_says()), Spend()
    )
    assert Caveat.NOT_LEGAL_ADVICE in out["caveats"]


def test_the_model_routes_out_of_scope_and_computation_and_diff() -> None:
    oos = parse.run(state(), given(parse_says(in_scope=False)), Spend())
    comp = parse.run(state(), given(parse_says(computation_requested=True)), Spend())
    diff = parse.run(
        state(),
        given(
            parse_says(query_kind="diff", as_of=date(2025, 6, 1), compare_as_of=date(2026, 6, 1))
        ),
        Spend(),
    )
    assert (oos["path"], comp["path"], diff["path"]) == ("out_of_scope", "refuse", "diff")
    assert diff["compare_as_of"] == date(2026, 6, 1)


def test_entity_type_and_as_of_are_set_exactly_once() -> None:
    node = run_node("parse", parse.run, Deps(models={"parse": parse_says()}, entities=ENTITIES))
    with pytest.raises(InvariantError, match="a second time"):
        node(state(as_of=TODAY))


# --------------------------------------------------------------------------- refuse_early


@pytest.mark.parametrize(
    ("path", "reason"),
    [("out_of_scope", RefusalReason.OUT_OF_SCOPE), ("refuse", RefusalReason.COMPUTATION_REQUESTED)],
)
def test_refuse_early_one_case_per_class(path: str, reason: RefusalReason) -> None:
    out = refuse_early.run(state(path=path, as_of=TODAY), Given(), Spend())
    assert out == {"outcome": "refusal", "refusal_reason": reason}


# --------------------------------------------------------------------------- expand


def test_expanded_rows_never_carry_a_fusion_score() -> None:
    out = expand.run(
        state(candidates=[cand(1)], neighbours=[cand(2, origin="expanded")]), Given(), Spend()
    )
    added = [c for c in out["candidates"] if c.expanded]
    assert [c.para_id for c in added] == [cand(2).para_id]
    assert all(c.fusion_score is None and c.rank is None for c in added)
    assert out["neighbours"] == []


# --------------------------------------------------------------------------- gate


def test_gate_drops_with_reasons_and_marks_itself_run() -> None:
    rows = [
        cand(1),
        cand(2, valid_from=date(2024, 1, 1), valid_to=date(2026, 1, 1)),
        cand(3, valid_from=date(2027, 1, 1)),
        cand(4, scope=("ucb",)),
    ]
    out = gate.run(state(candidates=rows, as_of=TODAY, entity_type="cb"), Given(), Spend())
    assert [c.para_id for c in out["candidates"]] == [rows[0].para_id]
    assert [d.reason for d in out["dropped"]] == [
        "superseded",
        "not_yet_effective",
        "out_of_entity_scope",
    ]
    assert out["gate_ran"] is True


def test_gate_refuses_superseded_only_when_only_stale_material_was_found() -> None:
    stale = cand(1, valid_from=date(2024, 1, 1), valid_to=date(2026, 1, 1))
    out = gate.run(state(candidates=[stale], as_of=TODAY), Given(), Spend())
    assert out["refusal_reason"] is RefusalReason.SUPERSEDED_ONLY
    empty = gate.run(state(candidates=[], as_of=TODAY), Given(), Spend())
    assert empty["refusal_reason"] is RefusalReason.NO_SUPPORT_FOUND


def test_after_the_gate_candidates_may_only_shrink() -> None:
    node = run_node("expand", lambda s, g, sp: {"candidates": [*s.candidates, cand(9)]}, Deps())
    with pytest.raises(InvariantError, match="after the gate"):
        node(state(candidates=[cand(1)], gate_ran=True, as_of=TODAY))


def test_a_removal_without_a_dropped_row_is_refused() -> None:
    node = run_node("gate", lambda s, g, sp: {"candidates": []}, Deps())
    with pytest.raises(InvariantError, match="no dropped row"):
        node(state(candidates=[cand(1)], as_of=TODAY))


# --------------------------------------------------------------------------- authority


def test_non_binding_only_material_refuses() -> None:
    faq = cand(1, authority_rank=60)
    out = authority.run(state(candidates=[faq], as_of=TODAY, gate_ran=True), Given(), Spend())
    assert out["refusal_reason"] is RefusalReason.NON_BINDING_SUPPORT_ONLY
    assert [d.reason for d in out["dropped"]] == ["non_binding_only"]
    assert out["set_aside"] == [faq]


def test_non_binding_beside_binding_stays_and_is_caveated_when_cited() -> None:
    rows = [cand(1), cand(2, authority_rank=60, body="FAQ: a bank keeps 9 per cent.")]
    s = state(candidates=rows, as_of=TODAY, gate_ran=True, entity_type="cb", path="full")
    assert authority.run(s, Given(), Spend()) == {}
    claim = DraftClaim(
        text="A bank keeps 9 per cent.",
        citation_ids=(rows[0].para_id, rows[1].para_id),
        quotes=("9 per cent", "keeps 9 per cent"),
    )
    verified = verify.run(s.model_copy(update={"draft_claims": [claim]}), Given(), Spend())
    done = finalize.run(
        s.model_copy(update={"draft_claims": [claim], **verified}), Given(), Spend()
    )
    assert Caveat.NON_BINDING_CITED in done["response"].result.caveats


def test_entity_scopes_that_disagree_refuse_entity_required_and_agreement_answers() -> None:
    differ = [cand(1, scope=("cb",)), cand(2, scope=("ucb",), doc="rbi:md:2025:0002:v1")]
    out = authority.run(state(candidates=differ, as_of=TODAY, path="full"), Given(), Spend())
    assert out["refusal_reason"] is RefusalReason.ENTITY_REQUIRED
    agree = [cand(1, scope=("cb", "ucb")), cand(2, scope=("cb",))]
    assert authority.run(state(candidates=agree, as_of=TODAY, path="full"), Given(), Spend()) == {}


# --------------------------------------------------------------------------- compose


def _compose_state() -> AgentState:
    return state(
        candidates=[cand(1), cand(2, "Banks shall report quarterly.")],
        as_of=TODAY,
        path="full",
        entity_type="cb",
        gate_ran=True,
    )


def test_compose_resolves_indices_to_para_ids_and_keeps_quotes_verbatim() -> None:
    model = composer_says(("Banks must report every quarter.", [1], ["report quarterly"]))
    out = compose.run(_compose_state(), given(model), Spend())
    (claim,) = out["draft_claims"]
    assert claim.citation_ids == (cand(2).para_id,)
    assert claim.quotes == ("report quarterly",)
    shown = model.calls[0][2]
    assert "[0]" in shown
    assert "[1]" in shown
    assert cand(1).para_id not in shown  # the model never sees an identifier it could copy


def test_an_index_out_of_range_fails_at_the_resolve_step() -> None:
    out = compose.run(_compose_state(), given(composer_says(("x", [7], ["x"]))), Spend())
    assert out["refusal_reason"] is RefusalReason.VERIFICATION_FAILED
    assert out["checks"]["index_in_range"].passed is False
    assert "draft_claims" not in out


def test_insufficient_is_reachable_and_becomes_no_support_found() -> None:
    model = composer_says(insufficient=True, missing="no paragraph sets a figure")
    out = compose.run(_compose_state(), given(model), Spend())
    assert out["refusal_reason"] is RefusalReason.NO_SUPPORT_FOUND
    assert out["missing"] == "no paragraph sets a figure"
    from extant.agent.prompts import COMPOSE_SYSTEM

    assert "insufficient" in COMPOSE_SYSTEM
    assert "correct response" in COMPOSE_SYSTEM


def test_an_unreadable_composer_output_refuses_rather_than_crashing() -> None:
    def broken(schema: type, s: str, u: str) -> ComposerOutput:
        return ComposerOutput.model_validate(
            {"claims": [{"text": "x", "candidate_indices": [], "quotes": []}]}
        )

    out = compose.run(_compose_state(), given(FakeModel(broken)), Spend())
    assert out["refusal_reason"] is RefusalReason.VERIFICATION_FAILED


# --------------------------------------------------------------------------- verify and judge


def test_verify_locates_quotes_and_sends_low_overlap_claims_to_the_judge() -> None:
    s = _compose_state().model_copy(
        update={
            "draft_claims": [
                DraftClaim(
                    text="A bank shall maintain a minimum capital ratio of 9 per cent.",
                    citation_ids=(cand(1).para_id,),
                    quotes=("minimum capital ratio of 9 per cent",),
                ),
                DraftClaim(
                    text="Lenders must file their returns with the regulator.",
                    citation_ids=(cand(2).para_id,),
                    quotes=("report quarterly",),
                ),
            ]
        }
    )
    out = verify.run(s, Given(), Spend())
    assert "refusal_reason" not in out
    assert out["residual"] == [1]
    cit = out["verified_claims"][0].citations[0]
    assert cand(1).body[cit.quote_start : cit.quote_end] == "minimum capital ratio of 9 per cent"
    rejected = judge.run(s.model_copy(update=out), given(judge_says(False)), Spend())
    assert rejected["refusal_reason"] is RefusalReason.VERIFICATION_FAILED
    passed = judge.run(s.model_copy(update=out), given(judge_says(True)), Spend())
    assert "refusal_reason" not in passed


def test_the_judge_sees_only_the_claim_and_its_paragraphs() -> None:
    s = _compose_state().model_copy(
        update={
            "raw_query": "SECRET QUESTION TEXT",
            "draft_claims": [
                DraftClaim(
                    text="Lenders file returns.",
                    citation_ids=(cand(2).para_id,),
                    quotes=("report quarterly",),
                )
            ],
            "residual": [0],
        }
    )
    model = judge_says(True)
    judge.run(s, given(model), Spend())
    (_, system, user) = model.calls[0]
    assert "SECRET QUESTION TEXT" not in user + system
    assert s.candidates[0].body not in user  # an uncited candidate is not shown
    assert s.candidates[1].body in user


def test_judge_with_nothing_residual_calls_nothing() -> None:
    model = judge_says(True)
    assert judge.run(_compose_state(), given(model), Spend()) == {}
    assert model.calls == []


# --------------------------------------------------------------------------- finalize


def test_finalize_rejects_an_answer_with_nothing_verified() -> None:
    node = run_node("finalize", finalize.run, Deps())
    # Two guards hold it: the Answer model needs a claim, the state needs a verified one.
    with pytest.raises(Exception, match=r"answer with no verified claims|at least 1 item"):
        node(state(as_of=TODAY, path="full"))


def test_finalize_builds_a_refusal_with_dropped_on_it() -> None:
    stale = cand(1, valid_from=date(2024, 1, 1), valid_to=date(2026, 1, 1))
    gated = gate.run(state(candidates=[stale], as_of=TODAY), Given(), Spend())
    s = state(as_of=TODAY, path="full", **gated)
    out = finalize.run(s, Given(), Spend())
    r = out["response"]
    assert r.result.outcome == "refusal"
    assert r.result.reason is RefusalReason.SUPERSEDED_ONLY
    assert [d.reason for d in r.dropped] == ["superseded"]
    assert r.result.nearest[0].para_id == stale.para_id
    assert "2026-01-01" in r.result.message
    assert "Check the instrument itself" in r.result.message


def test_parse_output_shape() -> None:
    assert set(ParseOutput.model_fields) == {
        "in_scope",
        "computation_requested",
        "query_kind",
        "entity_type",
        "as_of",
        "compare_as_of",
        "topic_terms",
    }
    assert set(ComposerOutput.model_fields) == {"claims", "insufficient", "missing"}
    assert set(ComposerClaim.model_fields) == {"text", "candidate_indices", "quotes"}
