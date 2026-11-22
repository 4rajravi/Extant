"""The judge's hand-label harness: drawn, read back, and no agreement until fully labelled."""

from __future__ import annotations

from pathlib import Path

from evals.judge_labels import Pair, agreement, draw, read


def test_pairs_round_trip_and_agreement_waits_for_every_label(tmp_path: Path) -> None:
    out = draw(
        [
            Pair("fx", "Claim: a\n\nParagraphs:\nb", True),
            Pair("fx", "Claim: c\n\nParagraphs:\nd", False),
        ],
        tmp_path / "pairs.md",
    )
    labels = read(out)
    assert labels == [(True, None), (False, None)]
    assert agreement(labels) is None
    text = out.read_text(encoding="utf-8")
    first = text.replace("**Human verdict:** \n", "**Human verdict:** supported\n", 1)
    both = first.replace("**Human verdict:** \n", "**Human verdict:** supported\n", 1)
    out.write_text(both, encoding="utf-8")
    assert read(out) == [(True, True), (False, True)]
    assert agreement(read(out)) == 0.5
