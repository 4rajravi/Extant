"""Where the RBI corpus lives on the website, and link discovery over cached pages.

Discovery reads bytes that acquisition already cached and pulls out links and the listing
rows needed to decide what to fetch next. It never fetches, and it never interprets the
regulatory text itself; that is the parser's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from selectolax.lexbor import LexborHTMLParser

BASE = "https://www.rbi.org.in/Scripts/"

MASTER_DIRECTIONS = BASE + "BS_ViewMasterDirections.aspx"
WITHDRAWN_CIRCULARS = BASE + "NotificationUserWithdrawnCircular.aspx"
# RBI/2025-26/100, DOR.RRC.REC.302/33-01-010/2025-26 of November 28, 2025: the withdrawal of
# 9,445 circulars. Linked from the Repeal and Saving paragraph of each direction.
WITHDRAWAL_NOTIFICATION = BASE + "NotificationUser.aspx?Id=13170&Mode=0"
# DoS.CO.PPG.66/11.01.005/2026-27 of July 31, 2026: the Department of Supervision's withdrawal
# of the circulars its directions consolidated. Linked from the repeal paragraph of each of them.
SUPERVISION_WITHDRAWAL_NOTIFICATION = BASE + "NotificationUser.aspx?Id=13663&Mode=0"
# The press release of November 28, 2025 announcing the 244 Master Directions.
CONSOLIDATION_PRESS_RELEASE = BASE + "BS_PressReleaseDisplay.aspx?prid=61705"
# The press release of October 10, 2025 placing the draft directions for comment, with the
# lists of circulars proposed for repeal and to be retained as standalone. Linked from the
# November 28 release.
DRAFT_PRESS_RELEASE = BASE + "BS_PressReleaseDisplay.aspx?prid=61407"
# Notifications retained as standalone rather than consolidated, linked from the same release.
STANDALONE_CIRCULARS = BASE + "BS_ViewListofstandalonecirculars.aspx"
# The Department of Regulation's Master Directions on one page, linked from the same release.
REGULATION_ALL_LISTING_ID = 401

# Entity category listings, as linked from the site's own menu. The Department of
# Regulation issued the 244 consolidated directions; the Department of Supervision lists
# its own, later, supervisory directions under the same eleven categories.
REGULATION_LISTING_IDS = tuple(range(403, 414))
SUPERVISION_LISTING_IDS = tuple(range(419, 430))


def listing_url(did: int) -> str:
    return f"{MASTER_DIRECTIONS}?did={did}"


def detail_url(direction_id: int) -> str:
    return f"{BASE}BS_ViewMasDirections.aspx?id={direction_id}"


def versions_url(direction_id: int) -> str:
    return f"{BASE}NotificationPreVersion.aspx?id={direction_id}"


def version_url(direction_id: int, histid: int) -> str:
    """One earlier consolidated version of a direction, as RBI's version history links it."""
    return f"{BASE}NotificationPreVersion.aspx?id={direction_id}&Histid={histid}"


def version_of(url: str) -> tuple[int, int] | None:
    """(direction page id, Histid) of an earlier version page, else None."""
    m = _VERSION.search(url)
    return (int(m.group(1)), int(m.group(2))) if m else None


def is_version_page(url: str) -> bool:
    return _VERSION.search(url) is not None


def is_direction_page(url: str) -> bool:
    """A Master Direction's current text: ``BS_ViewMasDirections.aspx?id=<n>``."""
    return _DETAIL_ID.search(url) is not None


def direction_id_of(url: str) -> int | None:
    m = _DETAIL_ID.search(url)
    return int(m.group(1)) if m else None


_DETAIL_ID = re.compile(r"BS_ViewMasDirections\.aspx\?id=(\d+)", re.IGNORECASE)
_DATE_HEADER = re.compile(r"[A-Z][a-z]{2} \d{1,2}, \d{4}")
_VERSION = re.compile(r"NotificationPreVersion\.aspx\?id=(\d+)&Histid=(\d+)", re.IGNORECASE)
# "(Updated as on October 19, 2026)", also "(updated as on October 7, 2026)" and "... 2026]".
_UPDATED_AS_ON = re.compile(r"updated\s+as\s+on\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE)
_HISTORY_LABEL = re.compile(r"Updated as on ([A-Z][a-z]{2} \d{1,2}, \d{4})")


@dataclass(frozen=True, slots=True)
class ListingRow:
    department: str  # "regulation" | "supervision"
    listing_id: int
    category: str  # verbatim from the listing's header row
    listed_date: date
    title: str
    direction_id: int
    pdf_url: str | None
    size_label: str | None  # as printed, e.g. "1440 kb"


def _parse(html: bytes) -> LexborHTMLParser:
    return LexborHTMLParser(html.decode("utf-8", errors="replace"))


def parse_listing(html: bytes, *, department: str, listing_id: int) -> list[ListingRow]:
    """Rows of one entity category listing. The table is a header row naming the category,
    then date header rows, each followed by one row per direction."""
    table = _parse(html).css_first("table.tablebg")
    if table is None:
        raise ValueError(f"listing {listing_id} has no table.tablebg")
    category: str | None = None
    listed: date | None = None
    rows: list[ListingRow] = []
    for tr in table.css("tr"):
        header = tr.css_first(".tableheader")
        if header is not None:
            text = header.text(strip=True)
            if _DATE_HEADER.fullmatch(text):
                listed = datetime.strptime(text, "%b %d, %Y").date()
            elif category is None:
                category = text
            else:
                raise ValueError(f"listing {listing_id}: unexpected header {text!r}")
            continue
        hrefs = [a.attributes.get("href") or "" for a in tr.css("a")]
        ids = [int(m.group(1)) for h in hrefs if (m := _DETAIL_ID.search(h))]
        if not ids:
            continue
        if category is None or listed is None:
            raise ValueError(f"listing {listing_id}: a direction row precedes its headers")
        cells = tr.css("td")
        pdfs = [h.strip() for h in hrefs if h.strip().lower().endswith(".pdf")]
        rows.append(
            ListingRow(
                department=department,
                listing_id=listing_id,
                category=category,
                listed_date=listed,
                title=cells[0].text(strip=True),
                direction_id=ids[0],
                pdf_url=pdfs[0] if pdfs else None,
                size_label=cells[-1].text(strip=True) if len(cells) > 1 else None,
            )
        )
    return rows


def direction_ids_linked_from(html: bytes) -> list[int]:
    """Every Master Direction detail page linked from a page, in order, without repeats."""
    seen: dict[int, None] = {}
    for a in _parse(html).css("a"):
        m = _DETAIL_ID.search(a.attributes.get("href") or "")
        if m:
            seen.setdefault(int(m.group(1)), None)
    return list(seen)


def updated_as_on(text: str) -> date | None:
    """The "Updated as on <date>" a direction's title carries, if any."""
    m = _UPDATED_AS_ON.search(text)
    if m is None:
        return None
    return datetime.strptime(re.sub(r"\s+", " ", m.group(1)), "%B %d, %Y").date()


@dataclass(frozen=True, slots=True)
class VersionLink:
    """One earlier consolidated version listed on a direction's version history page."""

    direction_id: int
    histid: int
    updated_as_on: date
    url: str


def parse_version_history(html: bytes, direction_id: int) -> list[VersionLink]:
    """The earlier versions a history page lists, in the page's order. A label that is not
    "Updated as on <Mon DD, YYYY>" raises: the version numbering depends on these dates."""
    out: list[VersionLink] = []
    for a in _parse(html).css("a"):
        href = a.attributes.get("href") or ""
        m = _VERSION.search(href)
        if m is None or int(m.group(1)) != direction_id:
            continue
        label = a.text(strip=True)
        lm = _HISTORY_LABEL.fullmatch(label)
        if lm is None:
            raise ValueError(f"direction {direction_id}: unexpected version label {label!r}")
        histid = int(m.group(2))
        out.append(
            VersionLink(
                direction_id,
                histid,
                datetime.strptime(lm.group(1), "%b %d, %Y").date(),
                version_url(direction_id, histid),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class PdfTarget:
    """A PDF the corpus needs, with enough context for a person to recognise it."""

    url: str
    title: str
    category: str | None
    direction_id: int | None
    listed_date: date | None


def pdf_targets(rows: list[ListingRow], notification_html: bytes | None) -> list[PdfTarget]:
    """Every PDF of a listed direction, of either department, plus the withdrawal
    notification's own PDF, in listing order and without repeats."""
    targets: dict[str, PdfTarget] = {}
    for r in rows:
        if r.pdf_url:
            targets.setdefault(
                r.pdf_url, PdfTarget(r.pdf_url, r.title, r.category, r.direction_id, r.listed_date)
            )
    if notification_html is not None:
        for url in pdf_links(notification_html):
            targets.setdefault(
                url, PdfTarget(url, "RBI/2025-26/100, withdrawal of circulars", None, None, None)
            )
    return list(targets.values())


def pdf_links(html: bytes, *, scope: str = "table.tablebg") -> list[str]:
    """PDF links inside the page's content table. Links in the site chrome around it, such as
    sidebar documents, are not part of the notice and are left out."""
    root = _parse(html).css_first(scope)
    if root is None:
        return []
    seen: dict[str, None] = {}
    for a in root.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if href.lower().endswith(".pdf"):
            seen.setdefault(href, None)
    return list(seen)
