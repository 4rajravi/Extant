"""Extraction, against PDFs generated in the test, never against the network."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from extant.core.hashing import sha256_hex_bytes
from extant.corpora.rbi import extract as ex
from extant.corpora.rbi.acquisition import CacheEntry, RawCache


def make_pdf(pages: list[str]) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def test_extracts_text_and_layout_per_page() -> None:
    data = make_pdf(["1. These Directions shall be called", "2. They come into force"])
    doc = ex.extract_pdf(data, source_sha256=sha256_hex_bytes(data), url="u")
    assert doc.status == "extracted"
    assert doc.page_count == 2
    assert [p.number for p in doc.pages] == [1, 2]
    assert "These Directions" in doc.pages[0].text
    span = doc.pages[1].blocks[0].lines[0].spans[0]
    assert span.text.startswith("2. They")
    assert span.size == 11.0
    assert doc.extractor_version == ex.EXTRACTOR_VERSION


def test_extraction_is_deterministic() -> None:
    data = make_pdf(["A. Applicability", "3. These Directions shall apply to banks."])
    first = ex.extract_pdf(data, source_sha256="s", url="u")
    second = ex.extract_pdf(data, source_sha256="s", url="u")
    assert first == second


def test_unopenable_document_is_a_failure_record_not_an_exception() -> None:
    doc = ex.extract_pdf(b"<html>a captcha page</html>", source_sha256="s", url="u")
    assert doc.status == "failed"
    assert doc.failures[0].page is None
    assert "not a PDF" in doc.failures[0].error


def test_a_failing_page_is_recorded_with_its_number(monkeypatch: pytest.MonkeyPatch) -> None:
    real = ex._page

    def flaky(page: pymupdf.Page, number: int) -> ex.Page:
        if number == 2:
            raise RuntimeError("broken content stream")
        return real(page, number)

    monkeypatch.setattr(ex, "_page", flaky)
    data = make_pdf(["one", "two", "three"])
    doc = ex.extract_pdf(data, source_sha256="s", url="u")
    assert doc.status == "partial"
    assert [p.number for p in doc.pages] == [1, 3]
    assert [(f.page, f.error) for f in doc.failures] == [(2, "RuntimeError: broken content stream")]


def test_empty_pages_are_kept_and_reported() -> None:
    data = make_pdf(["text", ""])
    doc = ex.extract_pdf(data, source_sha256="s", url="u")
    assert doc.page_count == 2
    assert doc.empty_pages == [2]


def _cache_pdf(root: Path, url: str, data: bytes) -> None:
    sha = sha256_hex_bytes(data)
    RawCache(root).put(
        CacheEntry(url, url, 200, "t", sha, len(data), "application/pdf", None, None), data
    )


def test_extract_all_caches_on_sha_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cache_pdf(tmp_path, "https://x/a.pdf", make_pdf(["a"]))
    _cache_pdf(tmp_path, "https://x/b.pdf", b"not a pdf at all")
    first = ex.extract_all(tmp_path)
    assert (first.considered, first.extracted, first.from_cache) == (2, 2, 0)
    assert len(first.failed) == 1
    assert "b.pdf" in first.failed[0]

    def must_not_run(*args: object, **kwargs: object) -> ex.ExtractedDocument:
        raise AssertionError("a cached document was extracted again")

    monkeypatch.setattr(ex, "extract_pdf", must_not_run)
    second = ex.extract_all(tmp_path)
    assert (second.extracted, second.from_cache) == (0, 2)
    assert second.failed == first.failed  # failures stay visible, not silently skipped


def test_extract_all_honours_sample(tmp_path: Path) -> None:
    for name in "abc":
        _cache_pdf(tmp_path, f"https://x/{name}.pdf", make_pdf([name]))
    assert ex.extract_all(tmp_path, sample=2).considered == 2


def test_cached_extraction_round_trips(tmp_path: Path) -> None:
    data = make_pdf(["1. Short title", "2. Commencement"])
    _cache_pdf(tmp_path, "https://x/a.pdf", data)
    ex.extract_all(tmp_path)
    path = ex.extract_path(tmp_path, sha256_hex_bytes(data))
    assert ex.load_extracted(path) == ex.extract_pdf(
        data, source_sha256=sha256_hex_bytes(data), url="https://x/a.pdf"
    )
