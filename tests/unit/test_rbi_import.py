"""Manual PDF import, offline: files a person downloaded are filed under their URLs."""

from __future__ import annotations

from pathlib import Path

import httpx
import pymupdf
import pytest

from extant.core.hashing import sha256_hex_bytes
from extant.corpora.rbi import sources
from extant.corpora.rbi.acquisition import RawCache
from extant.corpora.rbi.crawl import BlockedHosts, crawl
from extant.corpora.rbi.extract import extract_all
from extant.corpora.rbi.importer import file_name, import_pdfs, write_download_page
from tests.unit.test_rbi_acquisition import CHALLENGE, make_fetcher, site

BASE = "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/"
TARGETS = [BASE + "204MD.PDF", BASE + "203MD.PDF", BASE + "100NTADBC8AC018BE.PDF"]


def make_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data: bytes = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    folder = tmp_path / "downloads"
    folder.mkdir()
    return folder


def test_a_downloaded_pdf_is_filed_under_its_url(tmp_path: Path, downloads: Path) -> None:
    data = make_pdf("1. These Directions shall be called")
    (downloads / "204MD.PDF").write_bytes(data)
    cache = RawCache(tmp_path / "rbi")
    report = import_pdfs(cache, downloads, TARGETS, now=lambda: "2026-11-19T10:00:00+00:00")
    assert report.imported == [("204MD.PDF", BASE + "204MD.PDF")]
    entry = cache.get(BASE + "204MD.PDF")
    assert entry is not None
    assert (entry.origin, entry.imported_file, entry.status) == ("imported", "204MD.PDF", None)
    assert entry.content_type == "application/pdf"
    assert cache.read_bytes(entry) == data
    [row] = list(cache.manifest())
    assert (row.outcome, row.source_sha256, row.attempts) == ("imported", sha256_hex_bytes(data), 0)
    assert report.missing == [BASE + "203MD.PDF", BASE + "100NTADBC8AC018BE.PDF"]


@pytest.mark.parametrize("name", ["204md.pdf", "204MD (1).PDF", "204MD(2).pdf", "204MD-1.PDF"])
def test_browser_renamings_still_match(tmp_path: Path, downloads: Path, name: str) -> None:
    (downloads / name).write_bytes(make_pdf("x"))
    report = import_pdfs(RawCache(tmp_path / "rbi"), downloads, TARGETS)
    assert report.imported == [(name, BASE + "204MD.PDF")]


def test_a_saved_captcha_page_is_refused(tmp_path: Path, downloads: Path) -> None:
    (downloads / "204MD.PDF").write_bytes(CHALLENGE)
    cache = RawCache(tmp_path / "rbi")
    report = import_pdfs(cache, downloads, TARGETS)
    assert report.not_pdf == ["204MD.PDF"]
    assert cache.get(BASE + "204MD.PDF") is None
    assert list(cache.manifest()) == []


def test_unmatched_files_are_reported_not_guessed(tmp_path: Path, downloads: Path) -> None:
    (downloads / "Utkarsh2029.pdf").write_bytes(make_pdf("strategy"))
    (downloads / "notes.txt").write_text("ignored, not a pdf name")
    report = import_pdfs(RawCache(tmp_path / "rbi"), downloads, TARGETS)
    assert report.unmatched == ["Utkarsh2029.pdf"]
    assert report.imported == []


def test_reimport_is_idempotent(tmp_path: Path, downloads: Path) -> None:
    (downloads / "204MD.PDF").write_bytes(make_pdf("x"))
    cache = RawCache(tmp_path / "rbi")
    import_pdfs(cache, downloads, TARGETS)
    again = import_pdfs(cache, downloads, TARGETS)
    assert again.imported == []
    assert again.already == [("204MD.PDF", BASE + "204MD.PDF")]
    assert len(list(cache.manifest())) == 1


def test_different_bytes_conflict_unless_replace(tmp_path: Path, downloads: Path) -> None:
    cache = RawCache(tmp_path / "rbi")
    (downloads / "204MD.PDF").write_bytes(make_pdf("first"))
    import_pdfs(cache, downloads, TARGETS)
    (downloads / "204MD.PDF").write_bytes(make_pdf("second"))
    report = import_pdfs(cache, downloads, TARGETS)
    assert report.conflicts == [("204MD.PDF", BASE + "204MD.PDF")]
    first = cache.get(BASE + "204MD.PDF")
    replaced = import_pdfs(cache, downloads, TARGETS, replace=True)
    assert replaced.imported == [("204MD.PDF", BASE + "204MD.PDF")]
    assert cache.get(BASE + "204MD.PDF") != first


def test_targets_must_have_unique_file_names(tmp_path: Path, downloads: Path) -> None:
    clash = [BASE + "204MD.PDF", "https://elsewhere.example/204md.pdf"]
    with pytest.raises(ValueError, match="share the file name"):
        import_pdfs(RawCache(tmp_path / "rbi"), downloads, clash)


def test_imported_pdfs_are_extracted(tmp_path: Path, downloads: Path) -> None:
    (downloads / "204MD.PDF").write_bytes(make_pdf("2. These Directions shall come into force"))
    import_pdfs(RawCache(tmp_path / "rbi"), downloads, TARGETS)
    summary = extract_all(tmp_path / "rbi")
    assert (summary.pdfs_in_cache, summary.extracted, summary.failed) == (1, 1, [])


def test_crawl_counts_imported_pdfs_as_cached_without_the_network(
    tmp_path: Path, downloads: Path
) -> None:
    data = tmp_path / "rbi"
    BlockedHosts(data / "blocked_hosts.json").add("rbidocs.rbi.org.in", "u", "CAPTCHA")
    first, _, _ = make_fetcher(tmp_path, site)
    crawl(first, data, log=lambda _: None)  # fills the listing and notice pages
    (downloads / "204MD.PDF").write_bytes(make_pdf("x"))
    import_pdfs(RawCache(data), downloads, [BASE + "204MD.PDF"])

    def no_rbidocs(request: httpx.Request) -> httpx.Response:
        assert request.url.host != "rbidocs.rbi.org.in"
        return site(request)

    second, recorder, _ = make_fetcher(tmp_path, no_rbidocs)
    result = crawl(second, data, log=lambda _: None)
    pdfs = next(s for s in result.stages if s.name == "pdfs")
    assert pdfs.cached == 1
    assert recorder.requests == []


def test_notification_pdf_links_ignore_the_site_chrome() -> None:
    page = (
        b'<div class="sidebar"><a href="https://rbidocs.rbi.org.in/rdocs/content/pdfs/Utkarsh.pdf">'
        b'Utkarsh</a></div><table class="tablebg"><tr><td>'
        b'<a href="https://rbidocs.rbi.org.in/rdocs/notification/PDFs/100NT.PDF">pdf</a>'
        b"</td></tr></table>"
    )
    assert sources.pdf_links(page) == [
        "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/100NT.PDF"
    ]


def test_download_page_lists_every_link(tmp_path: Path) -> None:
    page = tmp_path / "list.html"
    write_download_page([(TARGETS[0], "Small Finance Banks | <Misc> & more")], page)
    text = page.read_text()
    assert f'href="{TARGETS[0]}"' in text
    assert file_name(TARGETS[0]) in text
    assert "&lt;Misc&gt; &amp; more" in text  # escaped, never injected
