"""The paragraph parser. Each synthetic page pins one rule seen in the corpus; the real fixture
pages check the properties every parse must have. Expected paragraph counts for real pages are
not asserted here: those come from people, in tests/unit/test_parser_gate.py."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from extant.core.identity import parse_para_id
from extant.corpora.rbi import parse as P  # noqa: N812
from extant.corpora.rbi.extract_html import extract_html

DOC = "rbi:md:2025:0204:v1"
TITLE = "Reserve Bank of India (Small Finance Banks \u2013 Miscellaneous) Directions, 2025"


def page(body: str, title: str = TITLE, date_line: str = "November 28, 2025") -> bytes:
    return (
        '<html><body><table class="td"><tr><td>'
        "<p>RBI/DoR/2025-26/204<br>DoR.SOG(SPE).REC.No.123/13-04-001/2025-26</p>"
        f"<p>{date_line}</p><p>Previous Versions</p>"
        f'<p class="head">{title}</p>'
        "<table class='tablebg'><tr><td>Table of Contents</td></tr>"
        "<tr><td>Chapter I</td></tr></table>"
        f"{body}</td></tr></table></body></html>"
    ).encode()


def parse(body: str, **kwargs: object) -> P.ParsedDocument:
    return P.parse_document(page(body, **kwargs), doc_id=DOC)  # type: ignore[arg-type]


def anchors(result: P.ParsedDocument) -> list[str]:
    return [parse_para_id(p.para_id).anchor for p in result.paragraphs]


BASIC = (
    "<p>In exercise of the powers conferred by Section 35A, RBI hereby issues these Directions.</p>"
    "<p>Chapter I \u2013 Preliminary</p>"  # a chapter heading set as a plain paragraph
    '<p class="head">A. Short Title and Commencement</p>'
    "<p>1. These Directions shall be called the Directions, 2025.</p>"
    "<p>2. These Directions shall come into force with immediate effect.</p>"
    '<p class="head">B. Applicability</p>'
    "<p>3. These Directions shall be applicable to Small Finance Banks.</p>"
    "<p>Provided that, paragraph 36 shall apply to Primary Dealers.</p>"
    '<p class="head">Chapter II \u2013 Role of the Board</p>'
    "<p>4. The Board shall approve a policy:</p><p>(1) on donations; and</p><p>(i) its limits.</p>"
    "<p>(Dr. A Sahoo)</p><p>Chief General Manager</p>"
)


def test_paragraphs_chapters_and_headings() -> None:
    r = parse(BASIC)
    assert r.status == P.PARSED
    assert anchors(r) == ["preamble", "p1", "p2", "p3", "p4"]
    by = {parse_para_id(p.para_id).anchor: p for p in r.paragraphs}
    assert by["p3"].body.endswith("Provided that, paragraph 36 shall apply to Primary Dealers.")
    assert (
        by["p4"].body
        == "4. The Board shall approve a policy:\n(1) on donations; and\n(i) its limits."
    )
    assert by["p1"].chapter == "I"
    assert by["p1"].heading_path == "Chapter I \u2013 Preliminary > A. Short Title and Commencement"
    assert by["p4"].chapter == "II"
    assert r.chapters == ["Chapter I \u2013 Preliminary", "Chapter II \u2013 Role of the Board"]
    assert r.closing_blocks == 2  # the signature and designation are not paragraph text


def test_metadata_is_read_from_the_page() -> None:
    r = parse(BASIC)
    assert r.issued_on == date(2025, 11, 28)
    assert (r.effective_from, r.effective_basis) == (date(2025, 11, 28), "issue date")
    assert r.applicability == ["sfb"]
    assert r.applicability_status == "extracted"
    assert r.document is not None
    assert r.document.title == TITLE
    assert r.document.parse_status == P.PARSED


def test_inserted_letters_and_spaced_letters() -> None:
    r = parse("<p>62. A.</p><p>62A. B.</p><p>62B. C.</p><p>63. D.</p><p>63 A. E.</p><p>64. F.</p>")
    assert anchors(r) == ["p62", "p62A", "p62B", "p63", "p63A", "p64"]


def test_a_lettered_number_ahead_of_its_base_is_a_sub_item() -> None:
    # "3[15A." inside paragraph 6's definitions: marker 3, RBI's amendment bracket, item 15A
    body = (
        "<p>6. In these Directions:</p>"
        '<p><sup><a href="#F3">3</a></sup>[15A. \u2018specified asset\u2019 means an asset;]</p>'
        "<p>7. Next.</p>"
    )
    r = parse(body)
    assert anchors(r) == ["p6", "p7"]
    assert "15A. \u2018specified asset\u2019" in r.paragraphs[0].body


def test_an_amended_paragraph_keeps_its_number() -> None:
    body = (
        '<p>15. A.</p><p><sup><a href="#F13">13</a></sup>[15A. In exercise of powers.]</p>'
        "<p>16. B.</p>"
    )
    r = parse(body)
    assert anchors(r) == ["p15", "p15A", "p16"]
    assert r.paragraphs[1].body.startswith("[15A. In exercise")  # the marker is gone


def test_numbered_table_notes_stay_in_their_paragraph() -> None:
    body = (
        "<p>9. Nine.</p><p>10. A bank shall disclose:</p>"
        "<table><tr><td>Item</td><td>Amount</td></tr></table>"
        "<p>1. For a bank the total shall match.</p><p>2. Amounts may not be exclusive.</p>"
        "<p>11. Eleven.</p>"
    )
    r = parse(body)
    assert anchors(r) == ["p9", "p10", "p11"]
    assert "1. For a bank the total shall match." in r.paragraphs[1].body
    assert "Item | Amount" in r.paragraphs[1].body
    assert [w.code for w in r.warnings].count("absorbed_numbered_block") == 2
    assert r.status == P.PARSED_WITH_WARNINGS


def test_footnotes_are_not_paragraphs_and_markers_leave_the_body() -> None:
    body = (
        '<p>1. <sup title="Substituted w.e.f. October 1, 2026"><a href="#F1">1</a></sup>'
        "These Directions shall come into force.</p>"
        "<p>2. Two.</p>"
        '<p id="F1">1 Substituted w.e.f. October 1, 2026, consequent to the Amendment.</p>'
    )
    r = parse(body)
    assert anchors(r) == ["p1", "p2"]
    assert r.paragraphs[0].body == "1. These Directions shall come into force."
    assert r.footnotes == 1


def test_numbered_heading_with_decimal_children() -> None:
    body = (
        '<p class="head">Chapter I \u2013 Preliminary</p>'
        '<p class="head">1. Short Title and Commencement</p>'
        "<p>These Directions shall be called the Directions.</p>"
        '<p class="head">2. Definitions</p>'
        "<p>2.1 In these Directions, words have their meanings.</p>"
        "<p>2.2 Other words are as in the Act.</p>"
        '<p class="head">3. Effective Date</p>'
        "<p>These Directions shall come into effect from January 01, 2026.</p>"
    )
    r = parse(body)
    assert anchors(r) == ["p1", "p2.1", "p2.2", "p3"]
    assert r.paragraphs[0].body == (
        "1. Short Title and Commencement\nThese Directions shall be called the Directions."
    )
    assert (r.effective_from, r.effective_basis) == (date(2026, 1, 1), "dated")


def test_sub_headings_inside_a_paragraph_do_not_split_it() -> None:
    body = (
        "<p>5. Promoters shall meet these criteria.</p>"
        '<p class="head">(1) Where promoters are individuals</p>'
        "<p>Each promoter should have ten years of experience.</p>"
        '<p class="head">Explanation:</p><p>Experience means banking experience.</p>'
        "<p>6. Six.</p>"
    )
    r = parse(body)
    assert anchors(r) == ["p5", "p6"]
    assert "(1) Where promoters are individuals\nEach promoter" in r.paragraphs[0].body
    assert r.status == P.PARSED


def test_a_formula_legend_is_not_a_heading() -> None:
    body = (
        "<p>40. Duration is computed as follows, where:</p><p>MD = Modified duration</p>"
        "<p>P = Price.</p><p>41. Next.</p>"
    )
    r = parse(body)
    assert anchors(r) == ["p40", "p41"]
    assert r.paragraphs[0].body.endswith("MD = Modified duration\nP = Price.")


def test_a_chapter_introduction_goes_with_the_chapters_first_paragraph() -> None:
    body = (
        '<p class="head">Chapter I \u2013 General</p><p>1. One.</p>'
        '<p class="head">Chapter II \u2013 Gold Loans</p>'
        "<p>Background: RBI has restricted lending against gold.</p>"
        "<p>2. A bank shall not lend against gold bullion.</p>"
    )
    r = parse(body)
    assert anchors(r) == ["p1", "p2"]
    assert r.paragraphs[0].body == "1. One."  # not carried back into chapter I
    assert r.paragraphs[1].body.startswith("Background: RBI has restricted lending")
    assert r.paragraphs[1].heading_path == "Chapter II \u2013 Gold Loans"
    assert [w.code for w in r.warnings] == ["chapter_introduction"]


def test_an_annex_printed_in_the_page_numbers_afresh() -> None:
    body = (
        "<p>1. One.</p><p>2. Two, as in Annex I.</p>"
        '<p class="head">Annex I</p><p>1. Annex first.</p><p>2. Annex second.</p>'
        '<p class="head">Annex II</p><p>A form with no numbered paragraphs.</p>'
    )
    r = parse(body)
    assert anchors(r) == ["p1", "p2", "annex1.p1", "annex1.p2", "annex2"]
    assert r.annexes == ["Annex I", "Annex II"]


def test_a_signature_needs_a_designation() -> None:
    body = (
        "<p>1. Report as below.</p><p>(Refer table below also for summary of exposures)</p>"
        "<p>2. Two.</p>"
    )
    r = parse(body)
    assert anchors(r) == ["p1", "p2"]  # the bracketed line did not end the document
    assert r.closing_blocks == 0


def test_a_numbering_gap_is_reported() -> None:
    r = parse("<p>1. One.</p><p>2. Two.</p><p>4. Four.</p>")
    assert anchors(r) == ["p1", "p2", "p4"]
    assert [(w.code, w.detail) for w in r.warnings] == [("numbering_gap", "2 then 4")]


def test_a_false_jump_is_text() -> None:
    body = (
        "<p>64. Sixty four.</p><p>100 per cent of the exposure shall be deducted.</p>"
        "<p>65. Sixty five.</p>"
    )
    r = parse(body)
    assert anchors(r) == ["p64", "p65"]
    assert "100 per cent of the exposure" in r.paragraphs[0].body


def test_unstated_dates_and_applicability_are_left_empty() -> None:
    body = (
        "<p>1. These Directions shall come into effect on the day these are placed on the official"
        " website of the Reserve Bank.</p><p>2. A bank shall do things.</p>"
    )
    r = parse(body)
    assert (r.effective_from, r.effective_basis) == (None, "not dated")
    assert (r.applicability, r.applicability_status) == ([], "missing")


def test_updated_as_on_and_clean_title() -> None:
    r = parse("<p>1. One.</p>", title=TITLE + " (updated as on October 7, 2026)")
    assert r.updated_as_on == date(2026, 10, 7)
    assert r.document is not None
    assert r.document.title == TITLE


def test_a_page_without_a_title_still_parses() -> None:
    raw = page("<p>1. One.</p><p>2. Two.</p>", title="c")
    r = P.parse_blocks(
        extract_html(raw, source_sha256="0" * 64, url="u"), doc_id=DOC, fallback_title=TITLE
    )
    assert anchors(r) == ["p1", "p2"]
    assert (r.title, r.title_source) == (TITLE, "listing")
    assert [w.code for w in r.warnings] == ["no_title"]


def test_no_body_and_failure_are_records_not_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = P.parse_document(b"<html><body>(1952 kb) Previous Versions</body></html>", doc_id=DOC)
    assert (empty.status, empty.paragraphs) == (P.NO_BODY, [])

    def boom(*args: object, **kwargs: object) -> P.ParsedDocument:
        raise RuntimeError("a bug")

    monkeypatch.setattr(P, "_parse", boom)
    failed = P.parse_document(page("<p>1. One.</p>"), doc_id=DOC)
    assert failed.status == P.FAILED
    assert failed.failure == "RuntimeError: a bug"


def test_an_oversized_paragraph_is_never_split() -> None:
    rows = "".join(f"<p>({i}) clause {i} of a very long paragraph.</p>" for i in range(1, 801))
    r = parse(f"<p>1. A long paragraph:</p>{rows}<p>2. Two.</p>")
    assert anchors(r) == ["p1", "p2"]
    assert r.paragraphs[0].body.count("\n") == 800
    assert len(r.paragraphs[0].body) > 30000


def test_a_number_alone_is_kept_not_dropped() -> None:
    r = parse("<p>1. One.</p><p>2. [*****]</p><p>3. Three.</p>")
    assert anchors(r) == ["p1", "p2", "p3"]
    assert r.paragraphs[1].body == "2. [*****]"


def test_version_documents_need_their_predecessor() -> None:
    v2 = "rbi:md:2025:0204:v2"
    raw = extract_html(page("<p>1. One.</p>"), source_sha256="0" * 64, url="u")
    assert P.parse_blocks(raw, doc_id=v2).document is None
    doc = P.parse_blocks(raw, doc_id=v2, supersedes=DOC).document
    assert doc is not None
    assert (doc.version_seq, doc.supersedes_doc_id) == (2, DOC)


# --------------------------------------------------------------------------- real pages

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REAL = [
    (FIXTURES / "rbi" / "direction_13105_sfb_miscellaneous.html", "rbi:md:2025:0204:v3"),
    (FIXTURES / "rbi-md-2025-0259-v1" / "source.html", "rbi:md:2025:0259:v1"),
    (FIXTURES / "rbi-md-2025-0175-v1" / "source.html", "rbi:md:2025:0175:v1"),
    (FIXTURES / "rbi-md-2025-0382-v1" / "source.html", "rbi:md:2025:0382:v1"),
]


@pytest.mark.parametrize(("path", "doc_id"), REAL, ids=lambda x: Path(str(x)).parent.name)
def test_real_pages_parse_deterministically_and_account_for_every_block(
    path: Path, doc_id: str
) -> None:
    raw = path.read_bytes()
    first = P.parse_document(raw, doc_id=doc_id)
    second = P.parse_document(raw, doc_id=doc_id)
    assert first.status in (P.PARSED, P.PARSED_WITH_WARNINGS)
    assert [p.para_id for p in first.paragraphs] == [p.para_id for p in second.paragraphs]
    assert first.paragraphs == second.paragraphs  # byte identical records
    blocks = extract_html(raw, source_sha256="0" * 64, url="u").blocks
    assert sum(first.block_accounting.values()) == len(blocks)
    assert len({p.para_id for p in first.paragraphs}) == len(first.paragraphs)
    assert all(p.doc_id == doc_id for p in first.paragraphs)


# --------------------------------------------------------------------------- applicability scope


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        pytest.param(
            "3. These Directions shall be applicable to Commercial Banks. For the purpose of these"
            " Directions, 'Commercial Banks' means banking companies (other than Small Finance"
            " Banks, Payments Banks, and Local Area Banks), corresponding new banks, and the State"
            " Bank of India.",
            ["cb"],
            id="bracketed other than",
        ),
        pytest.param(
            "3. These Directions shall be applicable to all banking companies, corresponding new"
            " banks and State Bank of India, and foreign banks operating as branch(es), excluding"
            " Small Finance Banks (SFBs), Local Area Banks (LABs), Payments Banks (PBs), and"
            " Regional Rural Banks (RRBs), hereinafter collectively referred to as 'banks'.",
            ["cb"],
            id="excluding list up to hereinafter",
        ),
        pytest.param(
            "3. These Directions shall be applicable to all categories of Non-Banking Financial"
            " Company [excluding Housing Finance Companies (HFCs)] (hereinafter 'NBFCs').",
            ["nbfc"],
            id="bracketed excluding with a nested abbreviation",
        ),
        pytest.param(
            "3. (1) These Directions shall be applicable to all NBFCs. (2) These Directions are"
            " not applicable for the following:\n(i) MGC registered with RBI under the scheme of"
            " Registration of Mortgage Guarantee Companies;\n(ii) NOFHC registered with the RBI"
            " as NBFC.\n(3) The provisions given in these Directions except paragraph 7, 14 and"
            " 16 shall apply to HFC registered with the RBI under the NHB Act, 1987.",
            ["nbfc", "nbfc_hfc"],
            id="not applicable to the following, up to the next sub-paragraph",
        ),
        pytest.param(
            "3. These Directions shall be applicable to all NBFCs. These Directions shall not be"
            " applicable for Non-Operative Financial Holding Company (NOFHC) registered with the"
            " RBI.",
            ["nbfc"],
            id="not applicable for one entity",
        ),
    ],
)
def test_applicability_subtracts_what_a_direction_excludes(text: str, codes: list[str]) -> None:
    assert P.applicability_codes(text) == codes


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        pytest.param(
            "3. These Directions, except 6(3) and 6(4), shall apply to the following:\n(a) NBFC-D"
            " registered with the RBI under the provisions of the RBI Act, 1934",
            "nbfc_d",
            id="except a paragraph number",
        ),
        pytest.param(
            "3. These Directions, except directions contained in Chapter IX, shall be applicable"
            " to Housing Finance Companies (hereinafter 'HFCs').",
            "nbfc_hfc",
            id="except a chapter",
        ),
        pytest.param(
            "3. These Directions shall apply to an NBFC-MFI. Provided that paragraphs 6(1), 6(2)"
            " and 17 shall not apply to an NBFC-MFI.",
            "nbfc_mfi",
            id="some paragraphs do not apply",
        ),
        pytest.param(
            "3. (i) The prudential regulations prescribed in these directions shall not be"
            " applicable for NBFC-BL having customer interface; however conduct-related"
            " regulations shall be applicable for NBFC-BL.",
            "nbfc_bl",
            id="regulations prescribed in these directions",
        ),
    ],
)
def test_an_exclusion_of_parts_keeps_the_entity_in_scope(text: str, kept: str) -> None:
    assert kept in P.applicability_codes(text)
