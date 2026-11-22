"""The CI gate: per set, per metric tolerances, zero for the invariants, and read only."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from evals import gate
from evals.baseline import build
from evals.gate import check
from tests.eval_fixtures import row


def results(ranks: list[int | None], **kw: Any) -> list[dict[str, Any]]:
    return [row(f"q{i}", para_rank=r, doc_rank=r, **kw) for i, r in enumerate(ranks)]


BASE_RANKS: list[int | None] = [1] * 60 + [3] * 20 + [None] * 20  # R@1 0.6, R@5 0.8


def baseline() -> dict[str, Any]:
    return build(results(BASE_RANKS))


def failures(found: list[gate.Finding]) -> list[str]:
    return [f"{f.set}:{f.metric}" for f in found if not f.passed]


def test_the_same_results_pass() -> None:
    assert failures(check(baseline(), results(BASE_RANKS))) == []


def test_a_drift_within_tolerance_passes() -> None:
    # One query of 100 slips from rank 1 to rank 3: recall@1 drops by 0.01, tolerance 0.02.
    ranks = [3, *BASE_RANKS[1:]]
    assert failures(check(baseline(), results(ranks))) == []


def test_a_regression_beyond_tolerance_fails() -> None:
    ranks = [None] * 5 + BASE_RANKS[5:]  # five queries lost entirely: recall drops by 0.05
    failed = failures(check(baseline(), results(ranks)))
    assert "temporal:recall@1_para" in failed
    assert "temporal:recall@20_para" in failed


def test_one_leaked_paragraph_fails_because_zero_tolerance_is_zero() -> None:
    rows = results(BASE_RANKS)
    rows[0]["entity_violations"] = 1
    assert failures(check(baseline(), rows)) == ["temporal:entity_violations"]
    rows[0]["entity_violations"], rows[1]["as_of_violations"] = 0, 1
    assert failures(check(baseline(), rows)) == ["temporal:as_of_violations"]


def test_a_baseline_that_tolerates_a_leak_is_refused() -> None:
    lax = baseline()
    lax["tolerances"]["entity_violations"] = 1
    with pytest.raises(ValueError, match="zero tolerance"):
        check(lax, results(BASE_RANKS))


def test_a_set_whose_size_changed_fails() -> None:
    assert "temporal:n" in failures(check(baseline(), results(BASE_RANKS[:-1])))


def test_a_missing_set_fails() -> None:
    assert failures(check(baseline(), results(BASE_RANKS, set_name="withdrawal")))


def test_other_arms_and_sets_are_judged_separately() -> None:
    rows = results(BASE_RANKS) + results([None] * 100, arm="dense-only")
    assert failures(check(baseline(), rows)) == []


def test_the_gate_never_writes_the_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_file = tmp_path / "baseline.json"
    base_file.write_text(json.dumps(baseline()), encoding="utf-8")
    before = base_file.read_bytes()
    res = tmp_path / "results.parquet"
    pq.write_table(pa.Table.from_pylist(results([None] * 100)), res)

    real_open = builtins.open

    def read_only_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(m in mode for m in "wax+"):
            raise AssertionError(f"the gate opened {file} for writing")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", read_only_open)
    monkeypatch.setattr(Path, "write_text", lambda *a, **k: pytest.fail("write_text"))
    monkeypatch.setattr(Path, "write_bytes", lambda *a, **k: pytest.fail("write_bytes"))
    assert gate.main(["--baseline", str(base_file), "--results", str(res)]) == 1
    assert base_file.read_bytes() == before


def test_the_gate_has_no_flag_that_could_rewrite_the_baseline() -> None:
    with pytest.raises(SystemExit):
        gate.main(["--baseline", "b.json", "--results", "r.parquet", "--update"])


def test_a_baseline_is_measured_on_dev_only() -> None:
    with pytest.raises(SystemExit, match="dev split"):
        build(results(BASE_RANKS, split="test"))
