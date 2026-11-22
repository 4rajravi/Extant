"""The four Phase 3 metrics against fixtures where the right answer is obvious."""

from __future__ import annotations

from datetime import date

import pytest

from evals import metrics
from evals.metrics import (
    PoolingError,
    SetRows,
    as_of_violations,
    bootstrap_mean,
    entity_violations,
    first_hit_rank,
    hit_at,
    reciprocal_rank,
    summarise,
)
from tests.eval_fixtures import row


def test_an_empty_result_finds_nothing() -> None:
    assert first_hit_rank([], {"a"}) is None
    assert not hit_at(None, 20)
    assert reciprocal_rank(None) == 0.0


def test_a_gold_paragraph_at_rank_one() -> None:
    rank = first_hit_rank(["a", "b", "c"], {"a"})
    assert rank == 1
    assert all(hit_at(rank, k) for k in (1, 5, 10, 20))
    assert reciprocal_rank(rank) == 1.0


def test_a_gold_paragraph_just_past_k() -> None:
    returned = [f"x{i}" for i in range(1, 6)] + ["gold"]  # rank 6
    rank = first_hit_rank(returned, {"gold"})
    assert rank == 6
    assert not hit_at(rank, 5)
    assert hit_at(rank, 10)
    assert reciprocal_rank(rank) == pytest.approx(1 / 6)


def test_no_gold_means_no_rank_rather_than_a_miss_at_one() -> None:
    assert first_hit_rank(["a"], set()) is None


def test_recall_and_mrr_over_hand_built_rows() -> None:
    rows = SetRows(
        [
            row("q1", para_rank=1),
            row("q2", para_rank=6),
            row("q3", para_rank=None),
            row("q4", para_rank=20),
        ]
    )
    s = summarise(rows)
    assert s["n"] == 4
    assert s["recall@1_para"] == 0.25
    assert s["recall@5_para"] == 0.25
    assert s["recall@10_para"] == 0.5
    assert s["recall@20_para"] == 0.75
    assert s["mrr_para"] == pytest.approx((1 + 1 / 6 + 0 + 1 / 20) / 4)


def test_document_level_rows_have_no_paragraph_recall_and_are_never_filled_in() -> None:
    rows = SetRows([row("q1", doc_rank=1, gold_paras=()), row("q2", doc_rank=None, gold_paras=())])
    s = summarise(rows)
    assert s["n_paragraph_gold"] == 0
    assert s["recall@1_para"] is None
    assert s["recall@1_para_n"] == 0
    assert s["recall@1_doc"] == 0.5


def test_entity_and_as_of_accuracy_are_exactly_one_on_a_clean_fixture() -> None:
    s = summarise(SetRows([row(f"q{i}", para_rank=1, returned=12) for i in range(5)]))
    assert s["entity_accuracy"] == 1.0
    assert s["as_of_accuracy"] == 1.0
    assert s["entity_violations"] == 0
    assert s["as_of_violations"] == 0


def test_a_single_planted_violation_is_caught_and_counted() -> None:
    rows = [row(f"q{i}", para_rank=1, returned=100) for i in range(9)]
    rows.append(row("q9", para_rank=1, returned=100, entity_violations=1, as_of_violations=1))
    s = summarise(SetRows(rows))
    assert s["entity_violations"] == 1
    assert s["as_of_violations"] == 1
    assert s["entity_accuracy"] == pytest.approx(0.999)  # reads as fine, which is why...
    assert s["entity_returned"] == 1000  # ...the count of violations sits beside it


def test_violation_counters_per_paragraph() -> None:
    assert entity_violations([("cb",), ("cb", "sfb"), ("nbfc",)], ["cb"]) == 1
    windows = [
        (date(2025, 1, 1), None),
        (date(2025, 1, 1), date(2026, 1, 1)),  # valid_to is exclusive: out on 2026-01-01
        (date(2026, 1, 1), None),  # valid_from is inclusive: in on 2026-01-01
        (date(2026, 1, 2), None),  # not yet in force
    ]
    assert as_of_violations(windows, date(2026, 1, 1)) == 2


def test_rows_from_two_sets_cannot_be_summarised_together() -> None:
    with pytest.raises(PoolingError, match="never pooled"):
        SetRows([row("q1", para_rank=1), row("q2", para_rank=1, set_name="withdrawal")])
    for key, value in (("arm", "dense-only"), ("split", "test"), ("corpus", "sample")):
        with pytest.raises(PoolingError):
            SetRows([row("q1"), row("q2", **{key if key != "arm" else "arm": value})])


def test_no_function_takes_more_than_one_set() -> None:
    public = [n for n in dir(metrics) if not n.startswith("_")]
    assert not [n for n in public if "pool" in n.lower() and n != "PoolingError"]
    assert not [n for n in public if "across" in n.lower() or "overall" in n.lower()]


def test_the_bootstrap_interval_is_deterministic_and_brackets_the_mean() -> None:
    values = [1.0] * 30 + [0.0] * 70
    a, b = bootstrap_mean(values), bootstrap_mean(values)
    assert a == b
    assert a.value == pytest.approx(0.3)
    assert a.low is not None
    assert a.high is not None
    assert a.low < 0.3 < a.high
    assert a.low > 0.15
    assert a.high < 0.45
    assert bootstrap_mean([]).value is None


def test_overlap_is_symmetric_and_missing_intervals_never_differ() -> None:
    narrow = bootstrap_mean([1.0] * 95 + [0.0] * 5)
    wide = bootstrap_mean([1.0] * 50 + [0.0] * 50)
    assert not narrow.overlaps(wide)
    assert not wide.overlaps(narrow)
    assert narrow.overlaps(bootstrap_mean([]))


def test_audited_precision_is_a_gap_until_every_pair_is_judged() -> None:
    assert metrics.audited_precision([]) is None
    assert metrics.audited_precision(["correct", None]) is None
    assert metrics.audited_precision(["correct", "correct", "wrong", "correct"]) == 0.75


@pytest.mark.parametrize(
    "fn",
    [
        metrics.citation_validity,
        metrics.groundedness,
        metrics.refusal_correctness,
        metrics.false_refusal_rate,
    ],
)
def test_answer_level_metrics_are_phase_6(fn: object) -> None:
    with pytest.raises(NotImplementedError, match="Phase 6"):
        fn(None, None)  # type: ignore[operator]


def test_cost_per_question_is_phase_6() -> None:
    with pytest.raises(NotImplementedError, match="Phase 6"):
        metrics.cost_per_question([])
