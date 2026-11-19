"""Step 2, extraction: page text and layout from cached PDFs, via PyMuPDF.

Cached under ``extract/v<EXTRACTOR_VERSION>/<sha[:2]>/<source_sha256>.json``, so changing
nothing makes a rerun a no-op, and bumping ``EXTRACTOR_VERSION`` reruns every document. A
document or page that cannot be extracted becomes a failure record carrying the page number;
it never aborts the run and is never silently dropped.

What is kept per page: the plain text in PyMuPDF's reading order, and every text block with
its lines and spans (bounding box, font, size, flags). Font size and the bold flag are what
a numbering parser needs to tell headings from body text, and the boxes let it strip running
headers and footers. pdfplumber is reserved for genuine tables and is not used here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from extant.corpora.rbi.acquisition import RawCache

EXTRACTOR_VERSION = "1"


@dataclass(frozen=True, slots=True)
class Span:
    text: str
    font: str
    size: float
    flags: int
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Line:
    bbox: tuple[float, float, float, float]
    spans: list[Span]


@dataclass(frozen=True, slots=True)
class Block:
    bbox: tuple[float, float, float, float]
    lines: list[Line]


@dataclass(frozen=True, slots=True)
class Page:
    number: int  # 1 based, as printed page numbers are
    width: float
    height: float
    text: str
    blocks: list[Block]


@dataclass(frozen=True, slots=True)
class PageFailure:
    page: int | None  # None when the document would not open at all
    error: str


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    source_sha256: str
    extractor_version: str
    url: str
    status: str  # extracted | partial | failed
    page_count: int
    pages: list[Page] = field(default_factory=list)
    failures: list[PageFailure] = field(default_factory=list)

    @property
    def empty_pages(self) -> list[int]:
        return [p.number for p in self.pages if not p.text.strip()]


def _bbox(raw: Any) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (round(float(v), 2) for v in raw)
    return (x0, y0, x1, y1)


def _page(page: pymupdf.Page, number: int) -> Page:
    layout = page.get_text("dict")  # type: ignore[no-untyped-call]
    blocks = [
        Block(
            bbox=_bbox(b["bbox"]),
            lines=[
                Line(
                    bbox=_bbox(line["bbox"]),
                    spans=[
                        Span(
                            text=s["text"],
                            font=s["font"],
                            size=round(float(s["size"]), 2),
                            flags=int(s["flags"]),
                            bbox=_bbox(s["bbox"]),
                        )
                        for s in line["spans"]
                    ],
                )
                for line in b["lines"]
            ],
        )
        for b in layout["blocks"]
        if b.get("type") == 0
    ]
    return Page(
        number=number,
        width=round(float(page.rect.width), 2),
        height=round(float(page.rect.height), 2),
        text=page.get_text("text"),  # type: ignore[no-untyped-call]
        blocks=blocks,
    )


def _failed(source_sha256: str, url: str, reason: str) -> ExtractedDocument:
    return ExtractedDocument(
        source_sha256, EXTRACTOR_VERSION, url, "failed", 0, failures=[PageFailure(None, reason)]
    )


def extract_pdf(data: bytes, *, source_sha256: str, url: str) -> ExtractedDocument:
    # PyMuPDF opens almost any bytes without complaint, an HTML challenge page included, so
    # the PDF header is checked first. The spec allows it anywhere in the first 1024 bytes.
    if b"%PDF-" not in data[:1024]:
        return _failed(source_sha256, url, "cannot open: not a PDF (no %PDF- header)")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
    except Exception as exc:
        return _failed(source_sha256, url, f"cannot open: {type(exc).__name__}: {exc}")
    if not doc.is_pdf:
        doc.close()  # type: ignore[no-untyped-call]
        return _failed(source_sha256, url, "cannot open: PyMuPDF did not read it as a PDF")
    pages: list[Page] = []
    failures: list[PageFailure] = []
    with doc:
        for index in range(doc.page_count):
            try:
                pages.append(_page(doc[index], index + 1))
            except Exception as exc:
                failures.append(PageFailure(index + 1, f"{type(exc).__name__}: {exc}"))
        count = doc.page_count
    status = "extracted" if not failures else ("partial" if pages else "failed")
    return ExtractedDocument(source_sha256, EXTRACTOR_VERSION, url, status, count, pages, failures)


# --------------------------------------------------------------------------- cache


def extract_path(data_dir: Path, source_sha256: str) -> Path:
    return (
        data_dir / "extract" / f"v{EXTRACTOR_VERSION}" / source_sha256[:2] / f"{source_sha256}.json"
    )


def load_extracted(path: Path) -> ExtractedDocument:
    raw = json.loads(path.read_text(encoding="utf-8"))
    pages = [
        Page(
            number=p["number"],
            width=p["width"],
            height=p["height"],
            text=p["text"],
            blocks=[
                Block(
                    bbox=tuple(b["bbox"]),
                    lines=[
                        Line(
                            bbox=tuple(line["bbox"]),
                            spans=[Span(**{**s, "bbox": tuple(s["bbox"])}) for s in line["spans"]],
                        )
                        for line in b["lines"]
                    ],
                )
                for b in p["blocks"]
            ],
        )
        for p in raw["pages"]
    ]
    return ExtractedDocument(
        raw["source_sha256"],
        raw["extractor_version"],
        raw["url"],
        raw["status"],
        raw["page_count"],
        pages,
        [PageFailure(**f) for f in raw["failures"]],
    )


@dataclass
class ExtractSummary:
    pdfs_in_cache: int = 0
    considered: int = 0
    extracted: int = 0
    from_cache: int = 0
    partial: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"extract v{EXTRACTOR_VERSION}: {self.pdfs_in_cache} PDFs in cache,"
            f" {self.considered} considered, {self.extracted} extracted now,"
            f" {self.from_cache} from cache, {len(self.partial)} partial,"
            f" {len(self.failed)} failed"
        )


def extract_all(data_dir: Path, *, sample: int | None = None) -> ExtractSummary:
    cache = RawCache(data_dir)
    pdfs = sorted(
        (
            e
            for e in cache.entries()
            if (e.content_type or "").lower().startswith("application/pdf")
        ),
        key=lambda e: e.url,
    )
    summary = ExtractSummary(pdfs_in_cache=len(pdfs))
    for entry in pdfs[:sample] if sample is not None else pdfs:
        summary.considered += 1
        path = extract_path(data_dir, entry.source_sha256)
        if path.exists():
            summary.from_cache += 1
            doc = load_extracted(path)
        else:
            doc = extract_pdf(
                cache.read_bytes(entry), source_sha256=entry.source_sha256, url=entry.url
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(doc), ensure_ascii=False), encoding="utf-8")
            summary.extracted += 1
        if doc.status == "partial":
            summary.partial.append(f"{entry.url} pages {[f.page for f in doc.failures]}")
        elif doc.status == "failed":
            summary.failed.append(f"{entry.url}: {doc.failures[0].error}")
    return summary
