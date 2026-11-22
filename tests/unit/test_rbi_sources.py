"""Listing discovery against a real, committed category page (Small Finance Banks, fetched
2026-11-18). Expected values were counted by hand from the page, not from this code."""

from datetime import date
from pathlib import Path

from extant.corpora.rbi import sources

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "rbi"
    / "listing_did404_small_finance_banks.html"
)


def rows() -> list[sources.ListingRow]:
    return sources.parse_listing(FIXTURE.read_bytes(), department="regulation", listing_id=404)


def test_listing_row_count_and_dates() -> None:
    parsed = rows()
    assert len(parsed) == 31
    assert sum(r.listed_date == date(2025, 11, 28) for r in parsed) == 30
    assert [r.direction_id for r in parsed if r.listed_date == date(2026, 3, 10)] == [13328]


def test_listing_category_label_is_verbatim() -> None:
    assert {r.category for r in rows()} == {"Small Finance Banks"}


def test_listing_row_fields() -> None:
    row = next(r for r in rows() if r.direction_id == 13105)
    assert row.title.startswith("Reserve Bank of India (Small Finance Banks \u2013 Miscellaneous)")
    assert row.pdf_url == "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/204MD.PDF"
    assert row.size_label == "1440 kb"


def test_every_row_has_a_unique_id_and_a_pdf() -> None:
    parsed = rows()
    assert len({r.direction_id for r in parsed}) == len(parsed)
    assert all(r.pdf_url for r in parsed)


def test_direction_links_are_discovered_in_order_without_repeats() -> None:
    ids = sources.direction_ids_linked_from(FIXTURE.read_bytes())
    assert len(ids) == len(set(ids))
    assert {r.direction_id for r in rows()} <= set(ids)
