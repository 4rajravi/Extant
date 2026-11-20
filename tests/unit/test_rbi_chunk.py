"""Step 5 for the RBI corpus: determinism over the parser gate fixtures, and validity windows.

The golden digests in ``tests/fixtures/rbi/chunks-golden.json`` pin the chunk rows of the
three fixture pages. When the chunker's output changes, either the change was meant (bump
``CHUNKER_VERSION`` and regenerate the digests) or it was not (fix it): the test cannot tell
which, so it fails until one of the two is done.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from extant.corpora.rbi.chunk import CHUNKER_VERSION, chunk_paragraphs, dumps, validity_windows
from extant.corpora.rbi.entities import CATEGORIES, NBFC_SUBTYPES
from extant.corpora.rbi.parse import parse_document
from extant.index.chunking import MAX_CHUNK_TOKENS

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = FIXTURES / "rbi" / "chunks-golden.json"
GATE = ("rbi-md-2025-0175-v1", "rbi-md-2025-0259-v1", "rbi-md-2025-0382-v1")
LISTED = {"rbi-md-2025-0175-v1": "sfb", "rbi-md-2025-0259-v1": "rrb", "rbi-md-2025-0382-v1": "pb"}
LABELS = {c.code: c.label for c in CATEGORIES} | {s.code: s.label for s in NBFC_SUBTYPES}


def fixture_chunks(name: str) -> str:
    """The canonical serialisation of one fixture page's chunk rows."""
    doc_id = name.replace("-", ":", 3).replace("-v", ":v")
    parsed = parse_document((FIXTURES / name / "source.html").read_bytes(), doc_id=doc_id)
    assert parsed.document is not None
    codes = parsed.applicability or [LISTED[name]]
    start = parsed.effective_from or parsed.issued_on
    assert start is not None
    rows = chunk_paragraphs(
        parsed.document,
        parsed.paragraphs,
        entity_codes=codes,
        entity_labels=[LABELS[c] for c in codes],
        valid_from=start,
        valid_to=None,
    )
    return "".join(dumps(r) + "\n" for r in rows)


@pytest.mark.parametrize("name", GATE)
def test_chunk_rows_are_byte_identical_across_runs(name: str) -> None:
    assert fixture_chunks(name) == fixture_chunks(name)


def test_chunk_rows_match_the_golden_digests_for_this_chunker_version() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["chunker_version"] == CHUNKER_VERSION, (
        "CHUNKER_VERSION changed: regenerate tests/fixtures/rbi/chunks-golden.json"
    )
    assert golden["max_chunk_tokens"] == MAX_CHUNK_TOKENS
    for name in GATE:
        text = fixture_chunks(name)
        got = {"chunks": text.count("\n"), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        assert got == golden["fixtures"][name], (
            f"{name}: chunk rows changed without a CHUNKER_VERSION bump"
        )


@pytest.mark.parametrize("name", GATE)
def test_body_is_the_regulators_text_and_embed_text_only_adds_a_header(name: str) -> None:
    doc_id = name.replace("-", ":", 3).replace("-v", ":v")
    parsed = parse_document((FIXTURES / name / "source.html").read_bytes(), doc_id=doc_id)
    bodies = {p.para_id: p.body for p in parsed.paragraphs}
    assert parsed.document is not None
    for line in fixture_chunks(name).splitlines():
        row = json.loads(line)
        assert row["body"] in bodies[row["para_id"]]
        header = row["embed_text"][: -len(row["body"])]
        assert row["embed_text"] == header + row["body"]
        assert header.startswith(parsed.document.title + "\n")
        assert "Applies to: " in header
        assert "Applies to: " not in bodies[row["para_id"]]


# --------------------------------------------------------------------------- validity


def chain(*starts: tuple[str | None, str | None, str | None]) -> tuple[list[Any], dict[str, Any]]:
    """Versions of one direction: (updated_as_on, issued_on, effective_from) per version."""
    versions, report = [], {}
    for i, (updated, issued, effective) in enumerate(starts, start=1):
        doc_id = f"rbi:md:2025:0001:v{i}"
        versions.append(
            {"direction_id": 1, "doc_id": doc_id, "version": i, "updated_as_on": updated}
        )
        report[doc_id] = {"issued_on": issued, "effective_from": effective}
    return versions, report


def test_each_version_ends_where_the_next_begins_and_the_current_one_is_open() -> None:
    versions, report = chain(
        ("2025-11-28", "2025-11-28", "2025-12-01"),
        ("2026-03-10", None, None),
        ("2026-07-01", "2026-06-30", None),
    )
    w = validity_windows(versions, report)
    assert w["rbi:md:2025:0001:v1"] == (date(2025, 12, 1), date(2026, 3, 10))
    assert w["rbi:md:2025:0001:v2"] == (date(2026, 3, 10), date(2026, 7, 1))
    assert w["rbi:md:2025:0001:v3"] == (date(2026, 7, 1), None)


def test_a_first_version_without_a_dated_commencement_starts_on_its_issue_date() -> None:
    versions, report = chain((None, "2025-11-28", None))
    assert validity_windows(versions, report)["rbi:md:2025:0001:v1"] == (date(2025, 11, 28), None)


def test_a_version_that_does_not_start_after_its_predecessor_is_refused() -> None:
    versions, report = chain(("2026-01-01", "2026-01-01", None), ("2026-01-01", None, None))
    with pytest.raises(ValueError, match="does not start after"):
        validity_windows(versions, report)
