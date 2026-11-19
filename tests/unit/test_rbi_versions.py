"""Versions: RBI's own history numbers them. Label and title strings are real ones."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from extant.core.hashing import sha256_hex_bytes
from extant.corpora.rbi import sources
from extant.corpora.rbi.acquisition import CacheEntry, RawCache
from extant.corpora.rbi.crawl import crawl
from extant.corpora.rbi.extract_html import extract_all_html
from extant.corpora.rbi.identifiers import (
    DirectionIdentity,
    IdentityError,
    number_versions,
)
from tests.unit.test_rbi_acquisition import make_fetcher, ok, site

HISTORY = (
    b"<html><body><table class='tablebg'><tr><td>"
    b"<a href='NotificationPreVersion.aspx?id=13105&Histid=334'>Updated as on Nov 28, 2025</a>"
    b"<a href='NotificationPreVersion.aspx?id=13105&Histid=360'>Updated as on Oct 01, 2026</a>"
    b"<a href='NotificationPreVersion.aspx?id=99999&Histid=1'>Updated as on Jan 01, 2026</a>"
    b"</td></tr></table></body></html>"
)
IDENT = DirectionIdentity(13105, "rbi:md:2025:0204", "RBI/DoR/2025-26/204", "masthead")
CURRENT = sources.detail_url(13105)


def test_history_lists_this_directions_versions_in_order() -> None:
    links = sources.parse_version_history(HISTORY, 13105)
    assert [(v.histid, v.updated_as_on) for v in links] == [
        (334, date(2025, 11, 28)),
        (360, date(2026, 10, 1)),
    ]
    assert links[0].url == sources.version_url(13105, 334)
    assert sources.version_of(links[0].url) == (13105, 334)


def test_an_unexpected_history_label_fails_loudly() -> None:
    page = b"<a href='NotificationPreVersion.aspx?id=1&Histid=2'>Previous version</a>"
    with pytest.raises(ValueError, match="unexpected version label"):
        sources.parse_version_history(page, 1)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Reserve Bank of India (Small Finance Banks \u2013 Miscellaneous) Directions, 2025"
         " (Updated as on October 19, 2026)", date(2026, 10, 19)),
        ("... Directions, 2025 (updated as on October 7, 2026)", date(2026, 10, 7)),
        ("... Directions, 2025 Updated as on October 01, 2026]", date(2026, 10, 1)),
        ("Reserve Bank of India (Credit Information Companies) Directions, 2025", None),
    ],
)  # fmt: skip
def test_updated_as_on_variants(title: str, expected: date | None) -> None:
    assert sources.updated_as_on(title) == expected


def test_no_history_is_one_version() -> None:
    [only] = number_versions(
        IDENT, [], current_url=CURRENT, current_updated=None, current_date_source="current title"
    )
    assert (only.version, only.doc_id, only.current, only.supersedes) == (
        1,
        "rbi:md:2025:0204:v1",
        True,
        None,
    )
    assert only.date_source == "none"


def test_history_then_current_chain() -> None:
    history = sources.parse_version_history(HISTORY, 13105)
    v1, v2, v3 = number_versions(
        IDENT,
        history,
        current_url=CURRENT,
        current_updated=date(2026, 10, 19),
        current_date_source="current title",
    )
    assert [v.doc_id for v in (v1, v2, v3)] == [
        "rbi:md:2025:0204:v1",
        "rbi:md:2025:0204:v2",
        "rbi:md:2025:0204:v3",
    ]
    assert [v.supersedes for v in (v1, v2, v3)] == [None, v1.doc_id, v2.doc_id]
    assert [v.current for v in (v1, v2, v3)] == [False, False, True]
    assert (v1.histid, v1.updated_as_on, v1.date_source) == (
        334,
        date(2025, 11, 28),
        "version history",
    )
    assert (v3.histid, v3.url, v3.updated_as_on) == (None, CURRENT, date(2026, 10, 19))


def test_dates_must_ascend_and_the_current_text_must_be_newest() -> None:
    history = sources.parse_version_history(HISTORY, 13105)
    with pytest.raises(IdentityError, match="not newer"):
        number_versions(
            IDENT, history, current_url=CURRENT, current_updated=date(2026, 10, 1),
            current_date_source="current title",
        )  # fmt: skip
    with pytest.raises(IdentityError, match="not ascending"):
        number_versions(
            IDENT, list(reversed(history)), current_url=CURRENT, current_updated=None,
            current_date_source="none",
        )  # fmt: skip


def test_crawl_fetches_earlier_versions_once(tmp_path: Path) -> None:
    def with_history(request: httpx.Request) -> httpx.Response:
        if str(request.url) == sources.versions_url(13105):
            return ok(HISTORY)
        return site(request)

    data = tmp_path / "rbi"
    fetcher, recorder, _ = make_fetcher(tmp_path, with_history)
    result = crawl(fetcher, data, log=lambda _: None)
    stage = next(s for s in result.stages if s.name == "earlier versions")
    assert (stage.wanted, stage.fetched) == (2, 2)
    assert {str(r.url) for r in recorder.requests} >= {
        sources.version_url(13105, 334),
        sources.version_url(13105, 360),
    }
    again, again_recorder, _ = make_fetcher(tmp_path, with_history)
    crawl(again, data, log=lambda _: None)
    assert again_recorder.requests == []


def test_extraction_covers_version_pages(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    page = (
        b"<html><body><table class='td'><tr><td><p>1. Short title</p></td></tr>"
        b"</table></body></html>"
    )
    for url in (sources.detail_url(13105), sources.version_url(13105, 334)):
        data = page + url.encode()
        cache.put(
            CacheEntry(
                url, url, 200, "t", sha256_hex_bytes(data), len(data), "text/html", None, None
            ),
            data,
        )
    summary = extract_all_html(tmp_path)
    assert (summary.pages_in_cache, summary.extracted, summary.no_body) == (2, 2, [])
