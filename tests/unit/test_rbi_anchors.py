"""Paragraph anchors, on printed numbers and annex headings that occur in the corpus."""

from __future__ import annotations

import pytest

from extant.core.identity import InvalidIdentifierError, make_para_id
from extant.corpora.rbi.anchors import PREAMBLE, annex_anchor, paragraph_anchor, printed_number


@pytest.mark.parametrize(
    ("printed", "anchor"),
    [("1.", "p1"), ("62A.", "p62A"), ("102D.", "p102D"), ("38 A.", "p38A"), ("4.1", "p4.1"),
     ("12.2", "p12.2"), ("7", "p7")],
)  # fmt: skip
def test_paragraph_anchor_from_the_printed_number(printed: str, anchor: str) -> None:
    assert paragraph_anchor(printed) == anchor
    assert make_para_id("rbi:md:2025:0204:v3", anchor).endswith("#" + anchor)


@pytest.mark.parametrize("printed", ["", "A.", "(1)", "(i)", "1 lakh", "62a.", "4..1"])
def test_text_that_is_not_a_paragraph_number(printed: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        paragraph_anchor(printed)


def test_inserted_letter_with_or_without_space_is_one_anchor() -> None:
    assert printed_number("38 A.") == printed_number("38A.") == "38A"


@pytest.mark.parametrize(
    ("label", "paragraph", "anchor"),
    [("Annex I", None, "annex1"), ("Annex III", None, "annex3"), ("Annex - II", "2.", "annex2.p2"),
     ("Annex \u2013 IV", None, "annex4"), ("Annex I", "8", "annex1.p8"), ("Annex", None, "annex"),
     ("SCHEDULE-I", None, "schedule1"), ("SCHEDULE- I", "3", "schedule1.p3"),
     ("Annex 5", None, "annex5"), ("Annexure 5", None, "annex5"), ("Annex A", None, "annexA"),
     ("Annex XII", None, "annex12"), ("Appendix IV", None, "appendix4")],
)  # fmt: skip
def test_annex_anchors(label: str, paragraph: str | None, anchor: str) -> None:
    assert annex_anchor(label, paragraph) == anchor


@pytest.mark.parametrize("label", ["Annexures", "Annex VX", "Chapter III", "Annex I Form A"])
def test_not_an_annex_heading(label: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        annex_anchor(label)


def test_preamble_is_a_valid_anchor() -> None:
    assert make_para_id("rbi:md:2025:0204:v3", PREAMBLE) == "rbi:md:2025:0204:v3#preamble"
