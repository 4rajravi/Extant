"""The committed Phase 1 outputs: baseline counts and label sets.

These read the files in ``evals/`` that ``python -m extant.corpora.rbi report`` writes and that
are committed, so they run without the corpus cache. They check that every label row is a
valid Phase 0 record, and that the files were regenerated after the last change to the
extractor, parser or miner version.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from extant.core.models import ConcordanceEntry, Withdrawal
from extant.corpora.rbi.extract_html import HTML_EXTRACTOR_VERSION
from extant.corpora.rbi.mine import MINER_VERSION
from extant.corpora.rbi.parse import PARSER_VERSION
from extant.corpora.rbi.report import _covers, _date_text

EVALS = Path(__file__).resolve().parents[2] / "evals"
LABELS = EVALS / "labels" / "rbi"
CODE = {
    "html_extractor": HTML_EXTRACTOR_VERSION,
    "parser": f"p{PARSER_VERSION}",
    "miner": f"m{MINER_VERSION}",
}


def _rows(name: str) -> list[dict[str, Any]]:
    text = (LABELS / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def test_baseline_counts_carry_the_compared_counts_and_tolerances() -> None:
    baseline = json.loads((EVALS / "baseline-counts.json").read_text(encoding="utf-8"))
    for key in ("documents", "paragraphs", "concordance", "withdrawals"):
        assert isinstance(baseline[key], int)
        assert baseline[key] >= 0
    assert baseline["tolerances"] == {"documents": 0.0, "paragraphs": 0.02, "concordance": 0.05}
    assert baseline["withdrawals"] == len(_rows("withdrawals.jsonl")) + len(
        _rows("withdrawals_supervision.jsonl")
    )
    assert baseline["concordance"] == len(_rows("supersession.jsonl"))


def test_committed_outputs_match_the_code_versions() -> None:
    """A version bump without rerunning ``report`` fails here, not in a later phase."""
    baseline = json.loads((EVALS / "baseline-counts.json").read_text(encoding="utf-8"))
    manifest = json.loads((LABELS / "MANIFEST.json").read_text(encoding="utf-8"))
    assert baseline["code"] == CODE
    assert {k: manifest[k] for k in CODE} == CODE
    assert set(manifest["sets"]) == {p.name for p in LABELS.glob("*.jsonl")}


@pytest.mark.parametrize("name", ["withdrawals.jsonl", "withdrawals_supervision.jsonl"])
def test_committed_withdrawals_are_valid_and_uniquely_keyed(name: str) -> None:
    records = [Withdrawal.model_validate(r) for r in _rows(name)]
    assert records
    assert len({r.withdrawal_key for r in records}) == len(records)


def test_no_circular_is_withdrawn_by_both_departments() -> None:
    dor = {r["withdrawal_key"] for r in _rows("withdrawals.jsonl")}
    dos = {r["withdrawal_key"] for r in _rows("withdrawals_supervision.jsonl")}
    assert not dor & dos


def test_committed_supersession_entries_are_valid_document_level_concordance() -> None:
    entries = [ConcordanceEntry.model_validate(r) for r in _rows("supersession.jsonl")]
    assert entries
    for e in entries:
        assert e.relation == "consolidated_into"
        assert e.new_para_id is None  # document level: no annex names a paragraph
        assert e.evidence_anchor is not None


def test_a_retained_circular_withdrawn_later_carries_the_withdrawal() -> None:
    withdrawn = {
        (r["canonical_ref"], r["circular_date"]): r
        for name in ("withdrawals.jsonl", "withdrawals_supervision.jsonl")
        for r in _rows(name)
        if r["canonical_ref"]
    }
    retained = _rows("retained.jsonl")
    assert retained
    for r in retained:
        w = withdrawn.get((r["canonical_ref"], r["circular_date"]))
        assert r["withdrawn_by_doc_id"] == (w["withdrawn_by_doc_id"] if w else None)
        assert r["withdrawn_on"] == (w["effective_on"] if w else None)


def test_an_nbfc_subtype_covers_the_nbfc_listing() -> None:
    assert _covers(["nbfc_hfc"], "nbfc")
    assert _covers(["cb"], "cb")
    assert not _covers(["sfb"], "cb")
    assert not _covers(["nbfc"], None)


def test_annex_dates_parse_or_are_left_empty() -> None:
    assert _date_text("November 28,\n2025") == date(2025, 11, 28)
    assert _date_text("28.11.2025") is None
    assert _date_text(None) is None
