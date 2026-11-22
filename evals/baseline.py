"""Write ``evals/baseline.json`` from a results file: the deliberate act the gate never does.

    uv run python -m evals.baseline --results evals/results/full.parquet

Run it, read the diff, and commit the file on its own with a message saying why the bar moved.
This file is not ``evals/baseline-counts.json``; ``evals/README.md`` says which baseline does
which job.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from evals.arms import GATED
from evals.gate import GATED_METRICS
from evals.metrics import SetRows, summarise

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "evals" / "baseline.json"
# Deterministic retrieval over committed query vectors repeats exactly, so these allow for a
# rebuilt index or a changed library moving a few queries, not for noise.
TOLERANCES = {"recall": 0.02, "mrr": 0.02, "entity_violations": 0, "as_of_violations": 0}


def build(rows: Sequence[Mapping[str, Any]], *, arm: str = GATED) -> dict[str, Any]:
    gated = [r for r in rows if r["arm"] == arm]
    splits = {r["split"] for r in gated}
    if splits != {"dev"}:
        raise SystemExit(f"error: a baseline is measured on the dev split only, got {splits}")
    sets: dict[str, Any] = {}
    for name in sorted({r["set"] for r in gated}):
        s = summarise(SetRows([r for r in gated if r["set"] == name]))
        sets[name] = {"n": s["n"], **{m: s[m] for m in GATED_METRICS}}
    return {
        "arm": arm,
        "split": "dev",
        "schema": sorted({r["schema"] for r in gated}),
        "tolerances": dict(TOLERANCES),
        "sets": sets,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.baseline")
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=BASELINE)
    args = parser.parse_args(argv)
    baseline = build(pq.read_table(args.results).to_pylist())
    args.out.write_text(json.dumps(baseline, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: read the diff, then commit it on its own")
    return 0


if __name__ == "__main__":
    sys.exit(main())
