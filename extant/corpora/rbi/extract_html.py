"""Step 2 for HTML: the direction pages' text and structure, as blocks in document order.

The HTML direction pages on ``www.rbi.org.in`` are the source of record (decided 19 November
2026; the PDFs sit behind a CAPTCHA). Each page's text lives in ``table.td``. This module turns
that markup into blocks and keeps everything a numbering parser needs. It interprets nothing:
which block is a chapter, a paragraph or a proviso is the parser's job.

Per block:

``kind``
    ``heading`` (``<p class="head">``, ``<h1>``..``<h6>``), ``paragraph`` (``<p>``),
    ``footnote`` (``<p class="footnote">``), ``list_item`` (``<li>``), ``table``, or ``text``
    for text sitting directly in a container. The class is a hint only: RBI marks some chapter
    headings as plain ``<p>``, so ``tag`` and ``css_class`` are kept verbatim as well.
``text``
    As a browser renders it: runs of ASCII whitespace collapse to one space, ``<br>`` becomes a
    newline, the ends are trimmed, and non-breaking spaces are kept. Superscript footnote
    markers stay in the text, so "1These" is what the page shows; their offsets are in
    ``markers``.
``markers``
    Each ``<sup>``: ``start`` and ``end`` offsets into ``text``, its ``text``, the ``href`` of a
    link inside it (``#F9``) and its ``title``, which on RBI pages often carries the amendment
    note, for example "Substituted with effect from May 08, 2026 vide ...".
``list_*``
    For list items: nesting depth, list type (``1``, ``a``, ``A``, ``i``, ``I`` or ``bullet``), the
    ordinal and the marker a browser would draw (``c.``). These numbers are not in the text.
``rows``
    For tables: the text of every cell, row by row.
``links``
    Every ``<a href>`` inside the block, its text and ``href``: cross references, footnote
    links, and annexes published as separate PDF attachments rather than in the page.
``ids``
    Element ``id`` and ``name`` attributes inside the block, the targets of the page's own links
    (``#F9``, ``#C3K``). With ``index`` they locate a block now that there are no page numbers.

Cached under ``extract/<HTML_EXTRACTOR_VERSION>/<sha[:2]>/<source_sha256>.json``. A page with
no ``table.td`` body is a ``no_body`` record, an unexpected error a ``failed`` record; neither
aborts the run and neither is dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from extant.corpora.rbi import sources
from extant.corpora.rbi.acquisition import RawCache

HTML_EXTRACTOR_VERSION = "html-3"  # html-2: blocks keep links; html-3: a table keeps its own id

_WS = re.compile(r"[ \t\n\r\f\v]+")
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP = {"script", "style", "noscript", "-comment", "hr"}
# Elements a browser starts on a new line when they sit inside other text.
_LINE_BREAKING = {
    "p",
    "div",
    "li",
    "table",
    "tr",
    "ol",
    "ul",
    "blockquote",
    *{f"h{i}" for i in range(1, 7)},
}
_BLOCKY = {"p", "ol", "ul", "table", "li", *_HEADINGS, "div", "blockquote", "center"}
# selectolax returns a new Python wrapper on every access, so nodes are compared by mem_id,
# never with "is".


@dataclass(frozen=True, slots=True)
class Link:
    text: str
    href: str


@dataclass
class _Found:
    ids: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Marker:
    start: int
    end: int
    text: str
    href: str | None
    title: str | None


@dataclass(frozen=True, slots=True)
class Block:
    index: int
    kind: str
    tag: str
    css_class: str | None
    text: str
    markers: list[Marker] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    list_depth: int | None = None
    list_type: str | None = None
    list_ordinal: int | None = None
    list_marker: str | None = None
    rows: list[list[str]] | None = None
    links: list[Link] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HtmlDocument:
    source_sha256: str
    extractor_version: str
    url: str
    status: str  # extracted | no_body | failed
    blocks: list[Block] = field(default_factory=list)
    error: str | None = None


# --------------------------------------------------------------------------- inline text


class _Text:
    """Builds rendered text the way a browser collapses whitespace, tracking offsets."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.markers: list[tuple[int, int, str, str | None, str | None]] = []

    def _last(self) -> str:
        return self.parts[-1][-1] if self.parts and self.parts[-1] else ""

    def add(self, raw: str) -> None:
        text = _WS.sub(" ", raw)
        if text.startswith(" ") and self._last() in ("", " ", "\n"):
            text = text[1:]
        if text:
            self.parts.append(text)
            self.length += len(text)

    def newline(self) -> None:
        if self.parts and self.parts[-1].endswith(" "):
            self.parts[-1] = self.parts[-1][:-1]
            self.length -= 1
        self.parts.append("\n")
        self.length += 1

    def finish(self) -> tuple[str, list[Marker]]:
        joined = "".join(self.parts)
        lead = len(joined) - len(joined.lstrip(" \n"))
        text = joined.strip(" \n")
        markers = []
        for start, end, mtext, href, title in self.markers:
            s, e = max(start - lead, 0), min(end - lead, len(text))
            if s < e:
                markers.append(Marker(s, e, mtext, href, title))
        return text, markers


def _inline(node: LexborNode, out: _Text, found: _Found, *, skip_blocks: bool) -> None:
    for child in node.iter(include_text=True):
        tag = child.tag
        if tag == "-text":
            out.add(child.text_content or "")
            continue
        if tag in _SKIP:
            continue
        for attr in ("id", "name"):
            value = child.attributes.get(attr)
            if value:
                found.ids.append(value)
        href = child.attributes.get("href") if tag == "a" else None
        if href:
            found.links.append(Link(_WS.sub(" ", child.text() or "").strip(), href.strip()))
        if skip_blocks and tag in ("ol", "ul", "table"):
            continue
        if tag == "br":
            out.newline()
        elif tag == "sup":
            start = out.length
            _inline(child, out, found, skip_blocks=skip_blocks)
            link = child.css_first("a")
            title = child.attributes.get("title") or (
                link.attributes.get("title") if link else None
            )
            href = link.attributes.get("href") if link else None
            marker_text = _WS.sub(" ", child.text() or "").strip()
            out.markers.append((start, out.length, marker_text, href, title))
        else:
            ends_line = out.length == 0 or "".join(out.parts).endswith("\n")
            if tag in _LINE_BREAKING and not ends_line:
                out.newline()
            elif tag in ("td", "th") and not ends_line:
                out.add(" ")
            _inline(child, out, found, skip_blocks=skip_blocks)


def _text_of(node: LexborNode, *, skip_blocks: bool = True) -> tuple[str, list[Marker], _Found]:
    out = _Text()
    found = _Found()
    for attr in ("id", "name"):
        value = node.attributes.get(attr)
        if value:
            found.ids.append(value)
    _inline(node, out, found, skip_blocks=skip_blocks)
    text, markers = out.finish()
    return text, markers, found


# --------------------------------------------------------------------------- list numbering


def _roman(n: int) -> str:
    out = ""
    for value, numeral in (
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
        (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
    ):  # fmt: skip
        while n >= value:
            out += numeral
            n -= value
    return out


def _alpha(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("a") + rem) + out
    return out


def list_marker(list_type: str, ordinal: int) -> str:
    """The marker a browser draws for an ordered list item."""
    if list_type == "bullet":
        return "•"
    if ordinal < 1 and list_type != "1":
        return f"{ordinal}."
    body = {
        "1": str(ordinal),
        "a": _alpha(ordinal),
        "A": _alpha(ordinal).upper(),
        "i": _roman(ordinal),
        "I": _roman(ordinal).upper(),
    }.get(list_type, str(ordinal))
    return f"{body}."


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


# --------------------------------------------------------------------------- blocks


class _Walker:
    def __init__(self) -> None:
        self.blocks: list[Block] = []

    def emit(self, **kwargs: Any) -> None:
        self.blocks.append(Block(index=len(self.blocks), **kwargs))

    def walk(self, node: LexborNode, depth: int = 0) -> None:
        stray = _Text()
        stray_found = _Found()

        def flush() -> None:
            nonlocal stray, stray_found
            text, markers = stray.finish()
            if text:
                self.emit(
                    kind="text",
                    tag=node.tag or "",
                    css_class=None,
                    text=text,
                    markers=markers,
                    ids=stray_found.ids,
                    links=stray_found.links,
                )
            stray, stray_found = _Text(), _Found()

        for child in node.iter(include_text=True):
            tag = child.tag
            if tag == "-text":
                stray.add(child.text_content or "")
                continue
            if tag in _SKIP:
                continue
            if tag in ("p", *_HEADINGS):
                flush()
                self.paragraph(child)
            elif tag in ("ol", "ul"):
                flush()
                self.list(child, depth + 1)
            elif tag == "table":
                flush()
                self.table(child)
            elif tag == "br":
                stray.newline()
            elif any(n.tag in _BLOCKY for n in child.traverse() if n.mem_id != child.mem_id):
                flush()
                self.walk(child, depth)
            else:
                _inline(child, stray, stray_found, skip_blocks=True)
        flush()

    def paragraph(self, node: LexborNode) -> None:
        css = node.attributes.get("class")
        kind = (
            "heading" if node.tag in _HEADINGS or css == "head"
            else "footnote" if css == "footnote"
            else "paragraph"
        )  # fmt: skip
        text, markers, found = _text_of(node)
        if text:
            self.emit(
                kind=kind,
                tag=node.tag or "",
                css_class=css,
                text=text,
                markers=markers,
                ids=found.ids,
                links=found.links,
            )
        # A list or table inside a <p> is invalid HTML but occurs; keep it as its own block.
        for inner in node.iter():
            if inner.tag in ("ol", "ul"):
                self.list(inner, 1)
            elif inner.tag == "table":
                self.table(inner)

    def list(self, node: LexborNode, depth: int) -> None:
        ordered = node.tag == "ol"
        list_type = (node.attributes.get("type") or "1") if ordered else "bullet"
        counter = _int(node.attributes.get("start"), 1)
        for li in node.iter():
            if li.tag != "li":
                continue
            counter = _int(li.attributes.get("value"), counter)
            item_type = li.attributes.get("type") or list_type if ordered else "bullet"
            text, markers, found = _text_of(li)
            self.emit(
                kind="list_item",
                tag="li",
                css_class=li.attributes.get("class"),
                text=text,
                markers=markers,
                ids=found.ids,
                links=found.links,
                list_depth=depth,
                list_type=item_type,
                list_ordinal=counter if ordered else None,
                list_marker=list_marker(item_type, counter),
            )
            for inner in li.iter():
                if inner.tag in ("ol", "ul"):
                    self.list(inner, depth + 1)
                elif inner.tag == "table":
                    self.table(inner)
                elif any(
                    n.tag in ("ol", "ul", "table")
                    for n in inner.traverse()
                    if n.mem_id != inner.mem_id
                ):
                    self.walk_nested(inner, depth)
            counter += 1

    def walk_nested(self, node: LexborNode, depth: int) -> None:
        for inner in node.iter():
            if inner.tag in ("ol", "ul"):
                self.list(inner, depth + 1)
            elif inner.tag == "table":
                self.table(inner)
            elif any(
                n.tag in ("ol", "ul", "table") for n in inner.traverse() if n.mem_id != inner.mem_id
            ):
                self.walk_nested(inner, depth)

    def table(self, node: LexborNode) -> None:
        rows: list[list[str]] = []
        found = _Found()
        for attr in ("id", "name"):
            value = node.attributes.get(attr)
            if value:
                found.ids.append(value)
        for tr in node.css("tr"):
            # A nested table's rows are not this table's rows; its text is folded into the
            # cell that holds it.
            parent_table = tr.parent
            while parent_table is not None and parent_table.tag != "table":
                parent_table = parent_table.parent
            if parent_table is None or parent_table.mem_id != node.mem_id:
                continue
            cells = []
            for cell in tr.iter():
                if cell.tag in ("td", "th"):
                    text, _, cell_found = _text_of(cell, skip_blocks=False)
                    cells.append(text)
                    found.ids.extend(cell_found.ids)
                    found.links.extend(cell_found.links)
            rows.append(cells)
        text = "\n".join(" | ".join(r) for r in rows)
        self.emit(
            kind="table",
            tag="table",
            css_class=node.attributes.get("class"),
            text=text,
            ids=found.ids,
            rows=rows,
            links=found.links,
        )


def extract_html(data: bytes, *, source_sha256: str, url: str) -> HtmlDocument:
    try:
        tree = LexborHTMLParser(data.decode("utf-8", errors="replace"))
        body = tree.css_first("table.td")
        cell = body.css_first("td") if body is not None else None
        if cell is None:
            return HtmlDocument(
                source_sha256,
                HTML_EXTRACTOR_VERSION,
                url,
                "no_body",
                error="no table.td body: the page carries no direction text",
            )
        walker = _Walker()
        walker.walk(cell)
        if not walker.blocks:
            return HtmlDocument(
                source_sha256, HTML_EXTRACTOR_VERSION, url, "no_body", error="table.td is empty"
            )
        return HtmlDocument(source_sha256, HTML_EXTRACTOR_VERSION, url, "extracted", walker.blocks)
    except Exception as exc:
        return HtmlDocument(
            source_sha256,
            HTML_EXTRACTOR_VERSION,
            url,
            "failed",
            error=f"{type(exc).__name__}: {exc}",
        )


# --------------------------------------------------------------------------- cache


def html_extract_path(data_dir: Path, source_sha256: str) -> Path:
    return (
        data_dir / "extract" / HTML_EXTRACTOR_VERSION / source_sha256[:2] / f"{source_sha256}.json"
    )


def load_html_document(path: Path) -> HtmlDocument:
    raw = json.loads(path.read_text(encoding="utf-8"))
    blocks = [
        Block(
            **{
                **b,
                "markers": [Marker(**m) for m in b["markers"]],
                "links": [Link(**k) for k in b.get("links", [])],
            }
        )
        for b in raw.pop("blocks")
    ]
    return HtmlDocument(**raw, blocks=blocks)


@dataclass
class HtmlExtractSummary:
    pages_in_cache: int = 0
    considered: int = 0
    extracted: int = 0
    from_cache: int = 0
    no_body: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    blocks: int = 0

    def __str__(self) -> str:
        return (
            f"extract {HTML_EXTRACTOR_VERSION}: {self.pages_in_cache} direction and version pages,"
            f" {self.considered} considered, {self.extracted} extracted now,"
            f" {self.from_cache} from cache, {len(self.no_body)} without a body,"
            f" {len(self.failed)} failed, {self.blocks} blocks"
        )


def extract_all_html(data_dir: Path, *, sample: int | None = None) -> HtmlExtractSummary:
    cache = RawCache(data_dir)
    # Current direction pages and earlier version pages: both carry their text in table.td.
    pages = sorted(
        (
            e
            for e in cache.entries()
            if sources.is_direction_page(e.url) or sources.is_version_page(e.url)
        ),
        key=lambda e: sources.version_of(e.url) or (sources.direction_id_of(e.url) or 0, 1 << 30),
    )
    summary = HtmlExtractSummary(pages_in_cache=len(pages))
    for entry in pages[:sample] if sample is not None else pages:
        summary.considered += 1
        path = html_extract_path(data_dir, entry.source_sha256)
        if path.exists():
            summary.from_cache += 1
            doc = load_html_document(path)
        else:
            doc = extract_html(
                cache.read_bytes(entry), source_sha256=entry.source_sha256, url=entry.url
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(doc), ensure_ascii=False), encoding="utf-8")
            summary.extracted += 1
        summary.blocks += len(doc.blocks)
        if doc.status == "no_body":
            summary.no_body.append(entry.url)
        elif doc.status == "failed":
            summary.failed.append(f"{entry.url}: {doc.error}")
    return summary
