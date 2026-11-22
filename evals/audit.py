"""The label audit: a person's verdicts on a seeded sample of each set's mined labels.

Mined labels contain mining errors, so a recall figure has a ceiling it cannot be read past:
against a gold set that is 90 percent right, a perfect retriever scores about 0.9. The miner's
precision is therefore judged by hand on 50 pairs per set (all of them where a set has fewer),
drawn deterministically from a seed, and reported beside every metric.

    uv run python -m evals.audit draw     # writes evals/label-audit/<set>.md, verdicts blank

Each pair ends in a ``**Verdict:**`` line for a person to complete with ``correct`` or
``wrong``. Nothing here fills one in, and the precision is None, shown as a gap, until every
pair in a file is judged. Redrawing refuses to overwrite a file that has verdicts in it.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.metrics import audited_precision
from evals.sets import SEED, EvalQuery, load_sets

AUDIT_DIR = Path(__file__).resolve().parent / "label-audit"
PAIRS = 50
_VERDICT = re.compile(r"^\*\*Verdict:\*\*[ \t]*(.*)$", re.MULTILINE)
# Who judged the file: a person's name, or a note that a model checked it. The table prints
# it, so a machine check is never read as a hand audit.
_AUDITOR = re.compile(r"^Audited by:[ \t]*(.*)$", re.MULTILINE)
VALID = ("correct", "wrong")


class AuditError(ValueError):
    pass


@dataclass(frozen=True)
class Audit:
    set: str
    pairs: int
    judged: int
    precision: float | None  # None until every pair is judged
    auditor: str | None = None  # from the file's "Audited by:" line


def audit_path(name: str) -> Path:
    return AUDIT_DIR / f"{name}.md"


def parse_verdicts(text: str) -> list[str | None]:
    out: list[str | None] = []
    for raw in _VERDICT.findall(text):
        word = raw.strip().split()[0].lower().strip(".,;:") if raw.strip() else ""
        if not word:
            out.append(None)
        elif word in VALID:
            out.append(word)
        else:
            raise AuditError(f"verdict {raw.strip()!r} is neither 'correct' nor 'wrong'")
    return out


def read_audit(name: str) -> Audit:
    path = audit_path(name)
    if not path.exists():
        return Audit(name, 0, 0, None)
    text = path.read_text(encoding="utf-8")
    verdicts = parse_verdicts(text)
    judged = sum(v is not None for v in verdicts)
    found = _AUDITOR.search(text)
    auditor = found.group(1).strip() if found and found.group(1).strip() else None
    return Audit(name, len(verdicts), judged, audited_precision(verdicts), auditor)


# --------------------------------------------------------------------------- drawing


def _units(queries: Sequence[EvalQuery]) -> list[list[EvalQuery]]:
    """The labels to audit: one per mined pair. A temporal version pair produced two
    queries from one label, so it is audited once."""
    by: dict[str, list[EvalQuery]] = defaultdict(list)
    for q in queries:
        by[q.query_id.rsplit("-", 1)[0] if q.set == "temporal" else q.query_id].append(q)
    return [by[k] for k in sorted(by)]


def _render_pair(i: int, unit: list[EvalQuery]) -> list[str]:
    q = unit[0]
    out = [f"## {i}. {q.query_id.rsplit('-', 1)[0] if q.set == 'temporal' else q.query_id}", ""]
    s = q.source
    if q.set == "temporal":
        old_w, new_w = s["old_window"], s["new_window"]
        out += [
            f"Direction {s['direction_id']}, paragraph `{s['anchor']}`.",
            "",
            f"- Old version `{s['old_doc_id']}`, in force {old_w[0]} to {old_w[1] or 'open'}:",
            f"  > {s['old_excerpt']}",
            f"- New version `{s['new_doc_id']}`, in force {new_w[0]} to {new_w[1] or 'open'}:",
            f"  > {s['new_excerpt']}",
            "",
            "Claim: these are the same provision in two consecutive versions of the direction,"
            " the text changed between them, and each was in force on the dates shown.",
            "Queries built from it: "
            + "; ".join(
                f"`{x.query_id}` as of {x.as_of}, gold `{x.gold_para_ids[0]}`" for x in unit
            ),
        ]
    elif q.set == "withdrawal":
        rows = s["rows"]
        out += [
            f"Circular `{rows[0]['circular_ref']}`, dated {rows[0]['circular_date']}:"
            f" {rows[0]['title']}",
            "",
            *(
                f"- withdrawn by `{r['withdrawn_by_doc_id']}`, announced {r['announced_on']},"
                f" effective {r['effective_on']}"
                for r in rows
            ),
            "",
            "Claim: RBI's withdrawal list names this circular as withdrawn by the document"
            " shown, and the reference and date above are the circular's own.",
        ]
    else:
        out += [
            f"Old circular `{s['old_ref']}`, dated {s['old_date']}: {s['old_title']}",
            f"- consolidated into `{s['new_doc_id']}` (evidence `{s['evidence_doc_id']}`,"
            f" {s['evidence_anchor']})",
            "",
            "Claim: the repeal annex of the new direction lists the old circular as repealed"
            " and consolidated into it.",
        ]
    out += ["", f"Query: {q.text}", "", "**Verdict:** ", ""]
    return out


def draw(name: str, queries: Sequence[EvalQuery], pairs: int = PAIRS, seed: int = SEED) -> Path:
    path = audit_path(name)
    if path.exists() and any(v is not None for v in parse_verdicts(path.read_text("utf-8"))):
        raise AuditError(f"{path} already has verdicts; it is a person's work, not regenerated")
    units = _units(queries)
    chosen = sorted(
        random.Random(f"{seed}:{name}").sample(range(len(units)), min(pairs, len(units)))
    )
    lines = [
        f"# Label audit: {name}",
        "",
        f"{len(chosen)} of the {len(units)} mined labels in the {name} set, drawn with seed"
        f" {seed}. For each one, check the claim against the source and complete the"
        " **Verdict:** line with `correct` or `wrong`, adding a short reason after a wrong."
        " The ablation table reads the miner's precision from this file and shows a gap until"
        " every verdict is filled in.",
        "",
        "Audited by: ",
        "",
    ]
    for i, idx in enumerate(chosen, start=1):
        lines += _render_pair(i, units[idx])
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.audit")
    parser.add_argument("command", choices=["draw", "status"])
    args = parser.parse_args(argv)
    sets = load_sets()
    for name, eval_set in sets.items():
        if args.command == "draw":
            path = draw(name, eval_set.queries)
            print(f"{name}: wrote {path.relative_to(AUDIT_DIR.parents[1])}")
        else:
            a = read_audit(name)
            shown = "not audited" if a.precision is None else f"precision {a.precision:.2f}"
            print(f"{name}: {a.judged} of {a.pairs} judged, {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
