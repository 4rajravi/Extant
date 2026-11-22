"""The graph end to end, on a scripted index and scripted models: every path, every refusal
class the graph raises, every caveat, and the cost log."""

from __future__ import annotations

from datetime import date

import pytest

from extant.agent.models import AgentState, Caveat, ReferenceRecord, RefusalReason, WithdrawalRecord
from extant.agent.runtime import NODES
from tests.agent_support import (
    TODAY,
    ScriptedIndex,
    cand,
    composer_says,
    judge_says,
    parse_says,
    run_graph,
)

BODY = "A bank shall maintain a minimum capital ratio of 9 per cent of its risk weighted assets."
CLAIM = (
    "A bank must keep a minimum capital ratio of 9 per cent.",
    [0],
    ["minimum capital ratio of 9 per cent"],
)
REF_Q = "What replaced circular DBOD.No.BP.BC.12/21.04.048/2014-15?"
REF = "DBOD.BP.BC.12/21.04.048/2014-15"
MENTION = cand(
    1,
    "This Direction repeals circular DBOD.No.BP.BC.12/21.04.048/2014-15 on capital.",
    doc="rbi:md:2025:0004:v1",
    origin="reference",
)


def nodes(state: AgentState) -> list[str]:
    return [n.node for n in state.node_log]


def full(**kw: object) -> AgentState:
    args: dict[str, object] = {
        "index": ScriptedIndex(ranked=[cand(1, BODY)]),
        "parse": parse_says(),
        "compose": composer_says(CLAIM),
        "judge": judge_says(),
    }
    return run_graph("What capital ratio must a bank keep?", **{**args, **kw})  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the log


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (
            lambda: full(),
            [
                "parse",
                "retrieve",
                "expand",
                "gate",
                "authority",
                "compose",
                "verify",
                "judge",
                "finalize",
            ],
        ),
        (lambda: run_graph("What is the GST on loans?"), ["parse", "refuse_early", "finalize"]),
        (lambda: run_graph("Calculate my bank's CRAR"), ["parse", "refuse_early", "finalize"]),
        (
            lambda: run_graph(
                REF_Q,
                index=ScriptedIndex(
                    refs=(
                        [MENTION],
                        [
                            ReferenceRecord(
                                surface="DBOD.No.BP.BC.12/21.04.048/2014-15",
                                canonical_ref=REF,
                                consolidated_into=("rbi:md:2025:0004:v1",),
                            )
                        ],
                    )
                ),
            ),
            [
                "parse",
                "resolve_reference",
                "gate",
                "authority",
                "compose",
                "verify",
                "judge",
                "finalize",
            ],
        ),
        (
            lambda: full(index=ScriptedIndex(ranked=[])),
            ["parse", "retrieve", "expand", "gate", "finalize"],
        ),
    ],
    ids=["full", "out_of_scope", "computation", "fast", "empty"],
)
def test_one_row_per_node_executed_including_zero_cost_rows(make, expected) -> None:  # type: ignore[no-untyped-def]
    state = make()
    assert nodes(state) == expected
    assert set(nodes(state)) <= set(NODES)
    assert all(n.eur >= 0 for n in state.node_log)
    zero = [n for n in state.node_log if n.calls == 0 and n.embedding_calls == 0]
    assert all(n.eur == 0 for n in zero)


def test_the_full_path_accounts_for_every_call() -> None:
    state = full()
    by = {n.node: n for n in state.node_log}
    assert (by["parse"].calls, by["compose"].calls, by["judge"].calls) == (1, 1, 0)
    assert by["retrieve"].embedding_calls == 1
    assert state.response is not None
    assert state.response.cost_eur == pytest.approx(sum(n.eur for n in state.node_log), abs=1e-6)


def test_the_fast_path_makes_zero_model_calls_and_zero_embedding_calls() -> None:
    p, c, j = parse_says(), composer_says(CLAIM), judge_says()
    idx = ScriptedIndex(
        refs=(
            [MENTION],
            [
                ReferenceRecord(
                    surface="DBOD.No.BP.BC.12/21.04.048/2014-15",
                    canonical_ref=REF,
                    consolidated_into=("rbi:md:2025:0004:v1",),
                )
            ],
        )
    )
    state = run_graph(REF_Q, index=idx, parse=p, compose=c, judge=j)
    assert state.path == "fast"
    assert state.outcome == "answer"
    assert (p.calls, c.calls, j.calls) == ([], [], [])
    assert all(n.calls == 0 and n.embedding_calls == 0 and n.eur == 0 for n in state.node_log)
    assert idx.calls == ["reference"]  # no search, so no embedding
    assert state.response is not None
    assert state.response.cost_eur == 0
    (claim,) = state.verified_claims
    cit = claim.citations[0]
    assert MENTION.body[cit.quote_start : cit.quote_end] == "DBOD.No.BP.BC.12/21.04.048/2014-15"


def test_a_fast_answer_on_an_unnumbered_annex_names_it_without_the_para_id() -> None:
    # Recorded on the real build: an annex row has no para_number, the template fell back to
    # the para_id, and the numeric check read its digits ("2025", "387") as quantities.
    annex = MENTION.model_copy(
        update={"para_id": "rbi:md:2025:0004:v1#annex3", "paragraph_number": None}
    )
    record = ReferenceRecord(
        surface="DBOD.No.BP.BC.12/21.04.048/2014-15",
        canonical_ref=REF,
        consolidated_into=("rbi:md:2025:0004:v1",),
    )
    state = run_graph(REF_Q, index=ScriptedIndex(refs=([annex], [record])))
    assert state.outcome == "answer", state.checks
    (claim,) = state.verified_claims
    assert "paragraph annex3 of" in claim.text
    assert "rbi:md" not in claim.text


@pytest.mark.parametrize(
    "text",
    [
        # Recorded on the real build: the composer wrote its own candidate number in prose.
        "Paragraph [0] requires a minimum capital ratio of 9 per cent.",
        "Paragraph rbi:md:2025:0001:v1#p1 requires a minimum capital ratio of 9 per cent.",
    ],
)
def test_a_claim_whose_prose_carries_an_internal_label_is_withheld(text: str) -> None:
    state = full(compose=composer_says((text, [0], ["minimum capital ratio of 9 per cent"])))
    assert state.refusal_reason is RefusalReason.VERIFICATION_FAILED
    assert state.refusal_detail == "the wording of a claim"
    assert not state.checks["claim0.plain_text"].passed


def test_an_out_of_scope_question_with_a_reference_exits_rather_than_taking_the_fast_path() -> None:
    idx = ScriptedIndex(refs=([MENTION], []))
    state = run_graph(
        "What is the GST treatment of circular DBOD.No.BP.BC.12/21.04.048/2014-15?", index=idx
    )
    assert state.path == "out_of_scope"
    assert state.refusal_reason is RefusalReason.OUT_OF_SCOPE
    assert idx.calls == []
    assert "resolve_reference" not in nodes(state)


def test_computation_outranks_a_reference_too() -> None:
    state = run_graph("Calculate my CRAR under circular DBOD.No.BP.BC.12/21.04.048/2014-15")
    assert state.refusal_reason is RefusalReason.COMPUTATION_REQUESTED


# --------------------------------------------------------------------------- the guarantees


def test_a_repealed_paragraph_never_reaches_compose() -> None:
    stale = cand(
        2,
        "STALE: a bank kept 8 per cent.",
        valid_from=date(2020, 1, 1),
        valid_to=date(2025, 1, 1),
        origin="probe",
    )
    composer = composer_says(CLAIM)
    state = full(
        index=ScriptedIndex(
            ranked=[cand(1, BODY), stale.model_copy(update={"origin": "ranked"})], probe=[stale]
        ),
        compose=composer,
    )
    (_, _, prompt) = composer.calls[0]
    assert "STALE" not in prompt
    assert stale.para_id in {d.para_id for d in state.dropped}
    assert state.response is not None
    assert stale.para_id in {d.para_id for d in state.response.dropped}


def test_a_composer_index_out_of_range_fails_at_resolve_and_refuses() -> None:
    state = full(compose=composer_says(("x", [3], ["minimum capital"])))
    assert state.refusal_reason is RefusalReason.VERIFICATION_FAILED
    assert state.draft_claims == []
    assert "verify" not in nodes(state)


def test_a_quote_that_is_not_a_substring_produces_a_refusal() -> None:
    state = full(compose=composer_says(("A bank must keep 9 per cent.", [0], ["keeps 9 per cent"])))
    assert state.refusal_reason is RefusalReason.VERIFICATION_FAILED
    assert state.checks["claim0.quote_match"].passed is False
    assert state.response is not None
    assert state.response.result.outcome == "refusal"


# --------------------------------------------------------------------------- every refusal class


def withdrawn(on: date) -> ReferenceRecord:
    return ReferenceRecord(
        surface="DBOD.No.BP.BC.12/21.04.048/2014-15",
        canonical_ref=REF,
        withdrawals=(
            WithdrawalRecord(
                withdrawn_by_doc_id="rbi:circ:2025:0100:v1",
                effective_on=on,
                title=None,
                circular_date=None,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda: run_graph("What is the income tax on interest?"), RefusalReason.OUT_OF_SCOPE),
        (
            lambda: run_graph("Calculate the provision for my bank"),
            RefusalReason.COMPUTATION_REQUESTED,
        ),
        (
            lambda: full(
                entity=None,
                index=ScriptedIndex(
                    ranked=[
                        cand(1, BODY, scope=("cb",)),
                        cand(2, BODY, scope=("ucb",), doc="rbi:md:2025:0002:v1"),
                    ]
                ),
            ),
            RefusalReason.ENTITY_REQUIRED,
        ),
        (
            lambda: full(compose=composer_says(insufficient=True, missing="no figure is set")),
            RefusalReason.NO_SUPPORT_FOUND,
        ),
        (
            lambda: run_graph(
                REF_Q, index=ScriptedIndex(refs=([], [withdrawn(date(2025, 11, 28))]))
            ),
            RefusalReason.SUPERSEDED_ONLY,
        ),
        (
            lambda: full(index=ScriptedIndex(ranked=[cand(1, BODY, authority_rank=60)])),
            RefusalReason.NON_BINDING_SUPPORT_ONLY,
        ),
        (
            lambda: full(
                compose=composer_says(
                    ("A bank keeps 12 per cent.", [0], ["minimum capital ratio of 9 per cent"])
                )
            ),
            RefusalReason.VERIFICATION_FAILED,
        ),
    ],
    ids=lambda v: v.value if isinstance(v, RefusalReason) else "",
)
def test_every_refusal_class_the_graph_raises_is_reachable(make, reason) -> None:  # type: ignore[no-untyped-def]
    state = make()
    assert state.refusal_reason is reason
    assert state.response is not None
    r = state.response.result
    assert r.outcome == "refusal"
    assert r.reason is reason
    assert (state.as_of or TODAY).isoformat() in r.message or reason in (
        RefusalReason.OUT_OF_SCOPE,
        RefusalReason.COMPUTATION_REQUESTED,
    )
    shows_nearest = reason in (
        RefusalReason.ENTITY_REQUIRED,
        RefusalReason.SUPERSEDED_ONLY,
        RefusalReason.NON_BINDING_SUPPORT_ONLY,
        RefusalReason.VERIFICATION_FAILED,
    )
    if not shows_nearest:
        assert r.nearest == []


def test_superseded_only_names_the_withdrawal_and_sends_the_reader_to_the_instrument() -> None:
    state = run_graph(REF_Q, index=ScriptedIndex(refs=([], [withdrawn(date(2025, 11, 28))])))
    assert state.response is not None
    msg = state.response.result.message  # type: ignore[union-attr]
    assert "rbi:circ:2025:0100:v1" in msg
    assert "2025-11-28" in msg
    assert msg.endswith("Check the instrument itself before relying on this.")


def test_a_withdrawal_after_the_date_asked_is_reported_as_may_still_have_applied() -> None:
    state = run_graph(
        REF_Q,
        index=ScriptedIndex(refs=([], [withdrawn(date(2025, 11, 28))])),
        as_of=date(2024, 3, 14),
    )
    assert state.refusal_reason is RefusalReason.NO_SUPPORT_FOUND
    assert state.response is not None
    msg = state.response.result.message  # type: ignore[union-attr]
    assert "after the date you asked about, so it may still have applied" in msg
    assert "2024-03-14" in msg


def test_cap_reached_is_defined_and_never_raised_by_the_graph() -> None:
    assert RefusalReason.CAP_REACHED.value == "cap_reached"
    import inspect

    from extant.agent import nodes

    for name in (
        "authority",
        "compose",
        "finalize",
        "gate",
        "judge",
        "parse",
        "refuse_early",
        "resolve_reference",
        "retrieve",
        "verify",
    ):
        source = inspect.getsource(
            getattr(nodes, name)
            if hasattr(nodes, name)
            else __import__(f"extant.agent.nodes.{name}", fromlist=["run"])
        )
        assert "CAP_REACHED" not in source


# --------------------------------------------------------------------------- every caveat


def test_the_caveats_attach_to_answers_without_replacing_them() -> None:
    assumed = full(as_of=None)
    assert assumed.outcome == "answer"
    assert Caveat.AS_OF_ASSUMED in assumed.caveats
    advice = run_graph(
        "Can I hold a capital ratio below 9 per cent?",
        index=ScriptedIndex(ranked=[cand(1, BODY)]),
        parse=parse_says(),
        compose=composer_says(CLAIM),
        judge=judge_says(),
    )
    assert advice.outcome == "answer"
    assert Caveat.NOT_LEGAL_ADVICE in advice.caveats
    pending = full(index=ScriptedIndex(ranked=[cand(1, BODY, valid_to=date(2027, 4, 1))]))
    assert pending.outcome == "answer"
    assert Caveat.AMENDMENT_PENDING in pending.caveats
    faq = cand(2, "FAQ: banks keep a minimum capital ratio of 9 per cent.", authority_rank=60)
    mixed = full(
        index=ScriptedIndex(ranked=[cand(1, BODY), faq]),
        compose=composer_says(
            (
                "A bank must keep a minimum capital ratio of 9 per cent.",
                [0, 1],
                ["minimum capital ratio of 9 per cent", "banks keep a minimum capital ratio"],
            )
        ),
    )
    assert mixed.outcome == "answer"
    assert Caveat.NON_BINDING_CITED in mixed.caveats

    class Degraded(ScriptedIndex):
        def search(self, *a, **k):  # type: ignore[no-untyped-def]
            found = super().search(*a, **k)
            return type(found)(found.candidates, found.neighbours, 0, 0, ["lexical_only"])

    degraded = full(index=Degraded(ranked=[cand(1, BODY)]))
    assert degraded.outcome == "answer"
    assert Caveat.DEGRADED_RETRIEVAL in degraded.caveats
    assert degraded.response is not None
    assert degraded.response.degraded == ["lexical_only"]
    for s in (assumed, advice, pending, mixed, degraded):
        assert s.response is not None
        assert s.response.result.outcome == "answer"
        assert set(s.response.result.caveats) == set(s.caveats)  # type: ignore[union-attr]


def test_the_diff_path_quotes_both_versions_with_one_model_call() -> None:
    old = cand(
        5,
        "The limit is 15 per cent.",
        doc="rbi:md:2025:0001:v1",
        valid_from=date(2025, 1, 1),
        valid_to=date(2026, 1, 1),
        asked_for=date(2025, 6, 1),
    )
    new = cand(
        5,
        "The limit is 20 per cent.",
        doc="rbi:md:2025:0001:v2",
        valid_from=date(2026, 1, 1),
        asked_for=date(2026, 6, 1),
        origin="diff",
    )
    p, c = (
        parse_says(query_kind="diff", as_of=date(2025, 6, 1), compare_as_of=date(2026, 6, 1)),
        composer_says(CLAIM),
    )
    state = run_graph(
        "How did the single borrower limit change?",
        as_of=None,
        index=ScriptedIndex(ranked=[old], other_version=new),
        parse=p,
        compose=c,
    )
    assert state.path == "diff"
    assert state.outcome == "answer"
    assert len(p.calls) == 1
    assert c.calls == []
    quoted = [cit.para_id for claim in state.verified_claims for cit in claim.citations]
    assert quoted == [old.para_id, new.para_id]


def _diff_with_one_version(valid_to: date | None) -> AgentState:
    only = cand(
        20,
        "20. The bank shall maintain a minimum capital adequacy ratio of 15 per cent.",
        valid_from=date(2025, 11, 28),
        valid_to=valid_to,
        asked_for=date(2026, 1, 15),
    )
    return run_graph(
        "How did the capital ratio change between 15 January 2026 and 20 November 2026?",
        as_of=None,
        index=ScriptedIndex(ranked=[only], other_version=only),
        parse=parse_says(
            query_kind="diff", as_of=date(2026, 1, 15), compare_as_of=date(2026, 11, 20)
        ),
    )


def test_a_diff_where_one_version_covers_both_dates_answers_that_nothing_changed() -> None:
    # Recorded on the real build: the same row was in force on both dates, and the diff path
    # refused with no_support_found saying no version was found, which was false.
    state = _diff_with_one_version(valid_to=None)
    assert state.outcome == "answer", state.refusal_detail
    (claim,) = state.verified_claims
    assert claim.text.startswith("The same version of paragraph 20 of")


def test_a_diff_with_no_version_on_the_second_date_refuses_and_says_so() -> None:
    state = _diff_with_one_version(valid_to=date(2026, 6, 1))
    assert state.refusal_reason is RefusalReason.NO_SUPPORT_FOUND
    assert state.refusal_detail is not None
    assert "no version in force on the second" in state.refusal_detail
