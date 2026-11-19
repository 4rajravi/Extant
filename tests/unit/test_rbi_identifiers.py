"""RBI number to doc_id base. Masthead strings are real ones from the corpus."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from extant.core.identity import InvalidIdentifierError, make_doc_base, parse_doc_id
from extant.corpora.rbi.extract_html import extract_html
from extant.corpora.rbi.identifiers import (
    IdentityError,
    circular_doc_base,
    financial_year,
    identify,
    identify_all,
    parse_rbi_number,
    pdf_serial,
)
from extant.corpora.rbi.sources import ListingRow

PDFS = "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/"


def row(direction_id: int, pdf: str | None, listed: date = date(2025, 11, 28)) -> ListingRow:
    return ListingRow(
        "regulation", 403, "Commercial Banks", listed, "title", direction_id,
        PDFS + pdf if pdf else None, "500 kb",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("masthead", "printed", "year", "serial"),
    [
        ("RBI/DoR/2025-26/204\nDoR.SOG(SPE).REC.No.123", "RBI/DoR/2025-26/204", 2025, 204),
        ("RBI/DOR/2025-26/288 DOR.FIN.REC.No.207/20-16-003", "RBI/DOR/2025-26/288", 2025, 288),
        ("RBI/2025-26/391 DOR.ACC.REC.No.428/21.02.067/2025-26", "RBI/2025-26/391", 2025, 391),
        ("RBI/DOR/2026-27/12 DOR.CAP.REC.No.5/21.06.201/2026-27", "RBI/DOR/2026-27/12", 2026, 12),
    ],
)  # fmt: skip
def test_rbi_numbers_from_real_mastheads(
    masthead: str, printed: str, year: int, serial: int
) -> None:
    number = parse_rbi_number(masthead)
    assert number is not None
    assert (number.text, number.financial_year, number.serial) == (printed, year, serial)


def test_rbi_number_absent_or_malformed() -> None:
    assert parse_rbi_number("DoR.SOG(SPE).REC.No.123/13-04-001/2025-26") is None
    with pytest.raises(IdentityError, match="not consecutive"):
        parse_rbi_number("RBI/DOR/2025-27/1")


@pytest.mark.parametrize(
    ("day", "year"),
    [(date(2025, 11, 28), 2025), (date(2026, 3, 31), 2025), (date(2026, 4, 1), 2026),
     (date(2025, 3, 31), 2024)],
)  # fmt: skip
def test_financial_year_runs_april_to_march(day: date, year: int) -> None:
    assert financial_year(day) == year


@pytest.mark.parametrize(
    ("url", "serial"),
    [(PDFS + "204MD.PDF", 204), (PDFS + "391MD10032026DEA2C8C1A3.PDF", 391),
     (PDFS + "151md.pdf", 151), (PDFS + "MD39857992A3DBE7946619F0F76DF81E472CF.PDF", None),
     (None, None)],
)  # fmt: skip
def test_pdf_file_name_serial(url: str | None, serial: int | None) -> None:
    assert pdf_serial(url) == serial


def test_identity_from_the_masthead() -> None:
    ident = identify(row(13105, "204MD.PDF"), "RBI/DoR/2025-26/204\nDoR.SOG(SPE).REC.No.123")
    assert (ident.doc_base, ident.rbi_number, ident.source) == (
        "rbi:md:2025:0204",
        "RBI/DoR/2025-26/204",
        "masthead",
    )


def test_identity_falls_back_to_file_name_and_listing_date() -> None:
    # 13159 has no text in its page; its listing carries 151MD.PDF, dated 28 November 2025
    ident = identify(row(13159, "151MD.PDF"), None)
    assert (ident.doc_base, ident.rbi_number, ident.source) == (
        "rbi:md:2025:0151",
        None,
        "pdf file name and listing date",
    )
    assert parse_doc_id(ident.doc_base + ":v1").serial == 151


def test_masthead_and_file_name_must_agree() -> None:
    with pytest.raises(IdentityError, match="disagrees"):
        identify(row(1, "205MD.PDF"), "RBI/DoR/2025-26/204")


def test_no_number_anywhere_fails_loudly() -> None:
    with pytest.raises(IdentityError, match="no RBI number"):
        identify(row(1, "MD39857992A3.PDF"), "no masthead here")


def test_two_directions_may_not_share_a_base() -> None:
    rows = [row(1, "204MD.PDF"), row(2, "204MD.PDF")]
    with pytest.raises(IdentityError, match="both map to"):
        identify_all(rows, {1: None, 2: None})


def test_the_withdrawal_notification() -> None:
    assert circular_doc_base("RBI/2025-26/100") == "rbi:circ:2025:0100"
    with pytest.raises(IdentityError):
        circular_doc_base("DOR.RRC.REC.302/33-01-010/2025-26")


def test_make_doc_base_validates_like_a_doc_id() -> None:
    assert make_doc_base("rbi", "md", 2025, 204) == "rbi:md:2025:0204"
    with pytest.raises(InvalidIdentifierError):
        make_doc_base("RBI", "md", 2025, 204)


def test_the_real_page_resolves_to_its_rbi_number() -> None:
    page = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "rbi"
        / "direction_13105_sfb_miscellaneous.html"
    )
    doc = extract_html(page.read_bytes(), source_sha256="s", url="u")
    assert identify(row(13105, "204MD.PDF"), doc.blocks[0].text).doc_base == "rbi:md:2025:0204"


def test_supervision_numbers() -> None:
    # The Department of Supervision prints its own series; RBI numbers one sequence per year,
    # so no Supervision serial is shared with a Regulation direction in the corpus.
    number = parse_rbi_number("RBI/DoS/2026-27/221 DoS.CO.PPG.66/11.01.005/2026-27")
    assert number is not None
    assert (number.text, number.financial_year, number.serial) == ("RBI/DoS/2026-27/221", 2026, 221)
    assert circular_doc_base("RBI/DoS/2026-27/221") == "rbi:circ:2026:0221"
