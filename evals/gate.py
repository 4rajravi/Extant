"""The CI gate: the gated arm's per set metrics against the committed baseline.

    uv run python -m evals.gate --baseline evals/baseline.json --results results.parquet

It fails on any per set regression beyond that metric's tolerance. Tolerances are per metric
because a one point recall drift and one leaked paragraph are different events: entity and
as-of violations have zero tolerance. It also fails when a set's n differs from the baseline's,
since the numbers then describe different questions.

It reads the baseline and never writes it, not even with a flag. A new baseline is a
deliberate commit of ``evals/baseline.json`` made with ``python -m evals.baseline``, so
improving the numbers and lowering the bar look different in a diff.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from evals.arms import GATED
from evals.metrics import SetRows, summarise

# Metric name -> kind. "higher" metrics fail below baseline minus tolerance; "count" metrics
# fail above baseline plus tolerance.
GATED_METRICS: dict[str, str] = {
    **{f"recall@{k}_{lvl}": "higher" for k in (1, 5, 10, 20) for lvl in ("para", "doc")},
    "mrr_para": "higher",
    "mrr_doc": "higher",
    "entity_violations": "count",
    "as_of_violations": "count",
}


@dataclass(frozen=True)
class Finding:
    set: str
    metric: str
    baseline: float | None
    value: float | None
    tolerance: float
    passed: bool

    def __str__(self) -> str:
        mark = "ok  " if self.passed else "FAIL"
        return (
            f"{mark} {self.set:<12} {self.metric:<20} baseline {_fmt(self.baseline)}"
            f" now {_fmt(self.value)} (tolerance {self.tolerance:g})"
        )


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.4f}"


def tolerance_for(metric: str, tolerances: Mapping[str, float]) -> float:
    family = metric.split("@")[0] if "@" in metric else metric.rsplit("_", 1)[0]
    if metric in tolerances:
        return float(tolerances[metric])
    if family in tolerances:
        return float(tolerances[family])
    raise KeyError(f"the baseline names no tolerance for {metric}")


def check(baseline: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    tolerances: Mapping[str, float] = baseline["tolerances"]
    for zero in ("entity_violations", "as_of_violations"):
        if float(tolerances.get(zero, -1)) != 0.0:
            raise ValueError(f"{zero} must have zero tolerance in the baseline")
    gated = [r for r in rows if r["arm"] == baseline.get("arm", GATED)]
    findings = []
    for name, expected in sorted(baseline["sets"].items()):
        mine = [r for r in gated if r["set"] == name]
        if not mine:
            findings.append(Finding(name, "present", 1.0, 0.0, 0.0, False))
            continue
        now = summarise(SetRows(mine))
        same_n = now["n"] == expected["n"]
        findings.append(Finding(name, "n", expected["n"], now["n"], 0.0, same_n))
        for metric, kind in GATED_METRICS.items():
            base, value = expected.get(metric), now.get(metric)
            if base is None and value is None:
                continue
            tol = tolerance_for(metric, tolerances)
            if base is None or value is None:
                ok = False
            elif kind == "higher":
                ok = value >= base - tol - 1e-12
            else:
                ok = value <= base + tol
            findings.append(Finding(name, metric, base, value, tol, ok))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.gate")
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    args = parser.parse_args(argv)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    rows = pq.read_table(args.results).to_pylist()
    findings = check(baseline, rows)
    for f in findings:
        print(f)
    failed = [f for f in findings if not f.passed]
    print(f"{len(findings) - len(failed)} passed, {len(failed)} failed against {args.baseline}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
