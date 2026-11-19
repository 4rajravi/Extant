"""The published lists RBI issued with the consolidation, read verbatim from cached pages.

These are the only gold labels the corpus supplies (decided 19 November 2026, "published lists
only"):

``withdrawn``
    "Circulars Withdrawn by the Department of Regulation", the live annex of RBI/2025-26/100.
``standalone``
    "List of Standalone Circulars issued by the Department of Regulation": retained, not
    consolidated.
``draft_repeal``
    "List of Circulars to be Repealed/Withdrawn By Department of Regulation", the draft stage of
    October 2025. A cross check only, never gold.
``withdrawn_supervision``, ``draft_repeal_supervision``
    The same two lists for the Department of Supervision, whose header reads ``Sr. No. |
    Circular Number | Subject | Date``.

Each list is a table ``S No. | Circular Number | Circular Name/Title | Date`` with footnotes in
single cell rows, such as "@-circulars shall be repealed with effect from January 01, 2026".
A row's footnote marks (``@``, ``*``, ``**``) are recorded, not stripped: cell text is kept as
published except that whitespace is collapsed, non-breaking spaces counting as spaces.
Nothing here normalises a reference; that is ``canonical_ref``'s job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from selectolax.lexbor import LexborHTMLParser, LexborNode

from extant.corpora.rbi import sources

LISTS: dict[str, tuple[str, str]] = {
    # name: (page URL, caption prefix of the table on that page)
    "withdrawn": (
        sources.WITHDRAWN_CIRCULARS,
        "Circulars Withdrawn by the Department of Regulation",
    ),
    "standalone": (
        sources.STANDALONE_CIRCULARS,
        "List of Standalone Circulars issued by the Department of Regulation",
    ),
    "draft_repeal": (
        sources.BASE + "BS_ViewCircularstobeRepealed.aspx",
        "List of Circulars to be Repealed/Withdrawn By Department of Regulation",
    ),
    # The Department of Supervision's directions are in the corpus too (decided 19 November 2026).
    "withdrawn_supervision": (
        sources.WITHDRAWN_CIRCULARS,
        "Circulars Withdrawn by the Department of Supervision",
    ),
    "draft_repeal_supervision": (
        sources.BASE + "BS_ViewCircularstobeRepealed.aspx",
        "List of Circulars to be Repealed/Withdrawn By Department of Supervision",
    ),
}
HEADER = ("Circular Number", "Date")
_WS = re.compile(r"\s+")
_MARKS = re.compile(r"(\*+|@+)\s*$")
_DATE_FORMATS = ("%B %d, %Y", "%B %d,%Y", "%b %d, %Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y")


@dataclass(frozen=True, slots=True)
class ListRow:
    list_name: str
    row: int  # position among the table's data rows, from 0; S No. can shift between visits
    serial: str  # S No. as printed
    ref: str  # "Circular Number" cell, rendered text, verbatim
    ref_href: str | None
    title: str  # verbatim, marks included
    date_text: str
    circular_date: date | None  # None when the printed date does not parse
    ref_marks: str | None  # trailing footnote marks on the reference, e.g. "**"
    title_marks: str | None  # trailing footnote marks on the title, e.g. "@"


@dataclass(frozen=True, slots=True)
class PublishedList:
    name: str
    caption: str
    rows: list[ListRow]
    footnotes: list[str] = field(default_factory=list)


def _text(node: LexborNode) -> str:
    return _WS.sub(" ", (node.text() or "").replace("\u00a0", " ")).strip()


def _parse_date(text: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _marks(text: str) -> str | None:
    m = _MARKS.search(text)
    return m.group(1) if m else None


def _caption(table: LexborNode) -> str:
    node = table.prev
    for _ in range(10):
        if node is None:
            return ""
        text = _text(node) if node.tag != "-text" else _WS.sub(" ", node.text() or "").strip()
        if text:
            return text
        node = node.prev
    return ""


def read_list(name: str, html: bytes) -> PublishedList:
    """Read one published list from its page. Raises ValueError if the table is not there or
    its header has changed, so a redesign of the page fails loudly instead of mining nothing."""
    _, caption_prefix = LISTS[name]
    tree = LexborHTMLParser(html.decode("utf-8", errors="replace"))
    for table in tree.css("table.tablebg"):
        caption = _caption(table)
        if not caption.startswith(caption_prefix):
            continue
        trs = table.css("tr")
        header = [_text(c) for c in trs[0].css("td,th")]
        if len(header) != 4 or header[1] != HEADER[0] or header[3] != HEADER[1]:
            raise ValueError(f"{name}: unexpected header {header}")
        rows: list[ListRow] = []
        footnotes: list[str] = []
        for tr in trs[1:]:
            cells = tr.css("td,th")
            if len(cells) == 1:
                footnotes.append(_text(cells[0]))
                continue
            if len(cells) != 4:
                raise ValueError(f"{name}: a row with {len(cells)} cells after row {len(rows)}")
            serial, ref_cell, title_cell, date_cell = cells
            link = ref_cell.css_first("a")
            ref, title, date_text = _text(ref_cell), _text(title_cell), _text(date_cell)
            rows.append(
                ListRow(
                    list_name=name,
                    row=len(rows),
                    serial=_text(serial),
                    ref=ref,
                    ref_href=(link.attributes.get("href") or None) if link else None,
                    title=title,
                    date_text=date_text,
                    circular_date=_parse_date(date_text),
                    ref_marks=_marks(ref),
                    title_marks=_marks(title),
                )
            )
        return PublishedList(name, caption, rows, footnotes)
    raise ValueError(f"{name}: no table captioned {caption_prefix!r}")
