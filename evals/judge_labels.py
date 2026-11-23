"""The judge's hand-label harness: claim and paragraph pairs with a person's verdict beside
the judge's.

    uv run python -m evals.judge_labels draw     # writes evals/judge-labels/pairs.md

Every judge call in the recorded agent fixtures (``tests/fixtures/agent``) becomes one pair: the
claim and paragraphs the judge saw, the judge's verdict, and a blank **Human verdict:** line
for a person (``supported`` or ``unsupported``). The agreement rate is a Phase 6 number; the
harness exists now because any metric that uses the judge is reported with that rate beside
it, so the labels have to be collectable before the metric is.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "agent"
LABELS = REPO / "evals" / "judge-labels" / "pairs.md"
_PAIR = re.compile(
    r"^- Judge verdict: (?P<judge>supported|unsupported)\n"
    r"\*\*Human verdict:\*\*[ \t]*(?P<human>.*)$",
    re.M,
)


@dataclass(frozen=True)
class Pair:
    source: str
    prompt: str  # exactly what the judge was shown
    judge: bool


def collect(fixtures: Path = FIXTURES) -> list[Pair]:
    pairs = []
    for path in sorted(fixtures.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("derived_from"):
            continue  # only real model responses are labelled
        for entry in data["models"].values():
            if entry["schema"] == "JudgeOutput":
                pairs.append(Pair(path.stem, entry["user"], bool(entry["parsed"]["supported"])))
    return pairs


def draw(pairs: Sequence[Pair], out: Path = LABELS) -> Path:
    lines = [
        "# Judge hand labels",
        "",
        "Each pair is exactly what the groundedness judge was shown. Complete the **Human"
        " verdict:** line with `supported` or `unsupported`, judging only whether the"
        " paragraphs support the claim. Phase 6 computes the judge's agreement rate from"
        " this file and reports it beside every metric that uses the judge.",
        "",
    ]
    for i, p in enumerate(pairs, start=1):
        lines += [
            f"## {i}. {p.source}",
            "",
            "```",
            p.prompt,
            "```",
            "",
            f"- Judge verdict: {'supported' if p.judge else 'unsupported'}",
            "**Human verdict:** ",
            "",
        ]
    if not pairs:
        lines.append("No judge calls in the recorded fixtures yet.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def read(path: Path = LABELS) -> list[tuple[bool, bool | None]]:
    """(judge, human) per pair; human is None until a person has judged it."""
    if not path.exists():
        return []
    out = []
    for m in _PAIR.finditer(path.read_text(encoding="utf-8")):
        human = m.group("human").strip().split(" ")[0].lower()
        out.append((m.group("judge") == "supported", None if not human else human == "supported"))
    return out


def agreement(labels: Sequence[tuple[bool, bool | None]]) -> float | None:
    """The judge's agreement with a person: None until every pair is labelled. Phase 6."""
    if not labels or any(h is None for _, h in labels):
        return None
    return sum(1 for j, h in labels if j == h) / len(labels)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.judge_labels")
    parser.add_argument("command", choices=["draw", "status"])
    args = parser.parse_args(argv)
    if args.command == "draw":
        print(f"wrote {draw(collect())}")
    else:
        labels = read()
        rate = agreement(labels)
        done = sum(h is not None for _, h in labels)
        print(
            f"{done} of {len(labels)} pairs labelled; agreement "
            f"{'not yet computable' if rate is None else f'{rate:.2f}'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
