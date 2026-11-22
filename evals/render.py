"""Render summary Parquet into the committed ablation table, ``evals/ablations.md``.

    uv run python -m evals.render evals/results/dev.summary.parquet

Dumb on purpose: it formats what the summary files say and computes nothing, so a metric is
defined in exactly one place (``evals.metrics``). One table per eval set, never one across
sets. The same input gives the same bytes, so a committed table changes only when a number
does.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from evals.arms import ARMS
from evals.metrics import DEPTHS

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "evals" / "ablations.md"
SETS_MANIFEST = REPO / "evals" / "sets" / "rbi" / "MANIFEST.json"


def _ci(s: Mapping[str, Any], name: str) -> str:
    value = s.get(name)
    if value is None:
        return "—"
    low, high = s.get(f"{name}_low"), s.get(f"{name}_high")
    return f"{value:.3f} [{low:.3f}, {high:.3f}]"


def _viol(s: Mapping[str, Any], name: str) -> str:
    returned = s.get(f"{name}_returned") or 0
    return f"{s.get(f'{name}_violations', 0)} of {returned}"


def _precision(s: Mapping[str, Any]) -> str:
    if s.get("label_precision") is None:
        pairs = s.get("audit_pairs") or 0
        judged = s.get("audit_judged") or 0
        return f"not audited ({judged} of {pairs} pairs judged): recall has an unknown ceiling"
    by = s.get("audit_by") or "an unnamed auditor"
    return f"{s['label_precision']:.2f} on {s['audit_pairs']} judged pairs, audited by {by}"


def _level(rows: Sequence[Mapping[str, Any]]) -> str:
    return "para" if any((r.get("n_paragraph_gold") or 0) > 0 for r in rows) else "doc"


def render_set(name: str, rows: Sequence[Mapping[str, Any]], notes: Mapping[str, str]) -> list[str]:
    """One set: its notes once, then one section per split. Splits are never mixed in a
    table: dev numbers are the ones arms were compared on, test numbers are the counted look."""
    splits = {str(r["split"]) for r in rows}
    # The count printed is the one the test run recorded, which is current; a dev run's copy
    # was read before it.
    test_rows = [r for r in rows if r["split"] == "test"]
    looks = (test_rows or list(rows))[0]["test_evaluations"]
    out = [f"## {name}", "", notes.get(name, ""), ""]
    for split in ("dev", "test"):
        if split in splits:
            out += render_split([r for r in rows if r["split"] == split], looks)
    return out


def render_split(rows: Sequence[Mapping[str, Any]], looks: int) -> list[str]:
    order = {a.name: i for i, a in enumerate(ARMS)}
    everything = sorted((r for r in rows if r["bucket"] == "all"), key=lambda r: order[r["arm"]])
    first = everything[0]
    level = _level(everything)
    unit = "paragraph" if level == "para" else "document"
    out = [
        f"### {first['split']} split",
        "",
        f"- Test-half evaluations so far: {looks}.",
        f"- Label precision: {_precision(first)}.",
        f"- Recall is {unit} level. Intervals are 95% bootstrap intervals; n is per row.",
    ]
    if level == "para":
        out.append(
            "- Document level recall is in its own columns and never averaged with paragraph level."
        )
    out += [
        "",
        "| Arm | Ablation | Corpus | n | "
        + " | ".join(f"R@{k}" for k in DEPTHS)
        + " | MRR | Doc R@1 | Doc R@10 | Entity violations | As-of violations | Differs from"
        " reference at |",
        "|" + "---|" * (10 + len(DEPTHS)),
    ]
    for r in everything:
        n = r["n_paragraph_gold"] if level == "para" else r["n"]
        cells = [
            f"`{r['arm']}`",
            r["ablation"],
            r["corpus"],
            str(n),
            *(_ci(r, f"recall@{k}_{level}") for k in DEPTHS),
            _ci(r, f"mrr_{level}"),
            _ci(r, "recall@1_doc"),
            _ci(r, "recall@10_doc"),
            _viol(r, "entity"),
            _viol(r, "as_of"),
            "reference" if r["compared_with"] is None else (r["differs_at"] or "—"),
        ]
        out.append("| " + " | ".join(cells) + " |")
    expanded = [r for r in everything if r["arm"] in ("hybrid", "no-expansion")]
    if level == "para" and len(expanded) == 2:
        out += [
            "",
            "With neighbourhood context counted (a gold paragraph returned as a neighbour of a"
            " top k result counts at that result's rank, so the returned set is larger than k):",
            "",
            "| Arm | " + " | ".join(f"R@{k} with context" for k in DEPTHS) + " |",
            "|---|" + "---|" * len(DEPTHS),
            *(
                f"| `{r['arm']}` | "
                + " | ".join(_ci(r, f"recall@{k}_para_ctx") for k in DEPTHS)
                + " |"
                for r in expanded
            ),
        ]
    holdout = sorted(
        (r for r in rows if r["arm"] == "hybrid" and r["bucket"] != "all"),
        key=lambda r: r["bucket"],
    )
    if holdout:
        out += [
            "",
            "Memorisation holdout, `hybrid` arm: queries whose gold was published after the"
            " cutoff, against the rest.",
            "",
            f"| Bucket | n | R@1 | R@10 | MRR ({unit}) |",
            "|---|---|---|---|---|",
        ]
        for r in holdout:
            n = r["n_paragraph_gold"] if level == "para" else r["n"]
            out.append(
                f"| {r['bucket']} | {n} | {_ci(r, f'recall@1_{level}')} |"
                f" {_ci(r, f'recall@10_{level}')} | {_ci(r, f'mrr_{level}')} |"
            )
    return [*out, ""]


def render(
    summaries: Sequence[Mapping[str, Any]], *, preamble: str, notes: Mapping[str, str]
) -> str:
    manifest = json.loads(SETS_MANIFEST.read_text(encoding="utf-8"))
    schemas = sorted({str(s["schema"]) for s in summaries})
    lines = [
        "# Retrieval ablations",
        "",
        "Generated by `python -m evals.render` from the runner's summary Parquet; do not edit"
        " by hand. Metrics are defined once, in `evals/metrics.py`.",
        "",
        f"- Builds: {', '.join(f'`{s}`' for s in schemas)}.",
        f"- Eval sets: builder {manifest['builder']}, parser {manifest['parser']}, miner"
        f" {manifest['miner']}, seed {manifest['seed']}.",
        f"- Memorisation holdout: material published after {manifest['holdout']['cutoff']}, the"
        f" training cutoff of {manifest['holdout']['model']}.",
        "- Not built: "
        + "; ".join(f"{k} ({v})" for k, v in sorted(manifest.get("not_built", {}).items()))
        + ".",
        "",
        preamble.strip(),
        "",
    ]
    by_set: dict[str, list[Mapping[str, Any]]] = {}
    for s in summaries:
        by_set.setdefault(str(s["set"]), []).append(s)
    for name in sorted(by_set):
        lines += render_set(name, by_set[name], notes)
    return "\n".join(lines).rstrip() + "\n"


NOTES_FILE = REPO / "evals" / "ablations-notes.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.render")
    parser.add_argument("summaries", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    rows = [r for p in args.summaries for r in pq.read_table(p).to_pylist()]
    notes = json.loads(NOTES_FILE.read_text(encoding="utf-8")) if NOTES_FILE.exists() else {}
    preamble = notes.pop("_preamble", "")
    args.out.write_text(render(rows, preamble=preamble, notes=notes), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
