"""RBI document identity: from a direction's RBI number to its doc_id base.

Decided 19 November 2026: the serial in a doc_id is the RBI number. A direction printed
``RBI/DoR/2025-26/204`` is ``rbi:md:2025:0204``; the year is the first year of RBI's financial
year (April to March), because that is the year the number belongs to. The version suffix is
added once versioning is settled.

Where the number comes from, in order:

1. The masthead, the page's first block: ``RBI/DoR/2025-26/204`` followed by the department
   number. 248 of the 249 listed directions carry it there.
2. Otherwise, the listing's PDF file name, whose leading digits are the serial (``151MD.PDF``),
   with the financial year of the listing date. This fallback was checked before it was trusted:
   on all 247 directions that have both, the file name serial equals the masthead serial, and on
   all 248 with a masthead the listing date's financial year equals the masthead's.

RBI prints the number in three series, ``RBI/DOR/``, ``RBI/DoR/`` and plain ``RBI/``. They do
not collide in the corpus, and any collision, or any disagreement between masthead and file
name, raises instead of guessing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from extant.core.identity import make_doc_base, parse_doc_id
from extant.corpora.rbi.sources import ListingRow, VersionLink

CORPUS = "rbi"
MASTER_DIRECTION = "md"
CIRCULAR = "circ"

RBI_NUMBER = re.compile(
    r"\bRBI/(?:(?P<dept>DoR|DOR|DoS|DOS)/)?(?P<fy>\d{4})-(?P<fy_end>\d{2})/(?P<serial>\d+)\b"
)
_PDF_SERIAL = re.compile(r"^(?P<serial>\d+)MD", re.IGNORECASE)


class IdentityError(ValueError):
    """A direction's identity is missing, contradictory or not unique."""


@dataclass(frozen=True, slots=True)
class RbiNumber:
    text: str  # as printed, e.g. "RBI/DoR/2025-26/204"
    financial_year: int  # its first year, 2025 for 2025-26
    serial: int


def parse_rbi_number(text: str) -> RbiNumber | None:
    m = RBI_NUMBER.search(text)
    if m is None:
        return None
    start = int(m["fy"])
    if (start + 1) % 100 != int(m["fy_end"]):
        raise IdentityError(
            f"financial year {m['fy']}-{m['fy_end']} is not consecutive in {text!r}"
        )
    return RbiNumber(m.group(0), start, int(m["serial"]))


def financial_year(d: date) -> int:
    """RBI's financial year runs April to March; this is its first year."""
    return d.year if d.month >= 4 else d.year - 1


def pdf_serial(pdf_url: str | None) -> int | None:
    if not pdf_url:
        return None
    m = _PDF_SERIAL.match(pdf_url.rsplit("/", 1)[-1])
    return int(m["serial"]) if m else None


@dataclass(frozen=True, slots=True)
class DirectionIdentity:
    direction_id: int  # RBI's page id, kept for traceability only
    doc_base: str  # rbi:md:<year>:<serial>
    rbi_number: str | None  # as printed in the masthead, when it is there
    source: str  # "masthead" | "pdf file name and listing date"


def identify(row: ListingRow, masthead: str | None) -> DirectionIdentity:
    number = parse_rbi_number(masthead) if masthead else None
    from_file = pdf_serial(row.pdf_url)
    if number is not None:
        if from_file is not None and from_file != number.serial:
            raise IdentityError(
                f"direction {row.direction_id}: masthead {number.text} disagrees with its PDF"
                f" file name {row.pdf_url}"
            )
        base = make_doc_base(CORPUS, MASTER_DIRECTION, number.financial_year, number.serial)
        return DirectionIdentity(row.direction_id, base, number.text, "masthead")
    if from_file is not None:
        year = financial_year(row.listed_date)
        base = make_doc_base(CORPUS, MASTER_DIRECTION, year, from_file)
        return DirectionIdentity(row.direction_id, base, None, "pdf file name and listing date")
    raise IdentityError(
        f"direction {row.direction_id} has no RBI number in its masthead and none in its PDF"
        f" file name {row.pdf_url!r}"
    )


def identify_all(
    rows: Iterable[ListingRow], mastheads: dict[int, str | None]
) -> list[DirectionIdentity]:
    """Identify every listed direction. Raises on a collision: two directions may never share
    a doc_id base."""
    out = [identify(r, mastheads.get(r.direction_id)) for r in rows]
    seen: dict[str, int] = {}
    for ident in out:
        if ident.doc_base in seen and seen[ident.doc_base] != ident.direction_id:
            raise IdentityError(
                f"directions {seen[ident.doc_base]} and {ident.direction_id} both map to"
                f" {ident.doc_base}"
            )
        seen[ident.doc_base] = ident.direction_id
    return sorted({i.direction_id: i for i in out}.values(), key=lambda i: i.doc_base)


def circular_doc_base(rbi_number: str) -> str:
    """The base id of a circular from its RBI number: RBI/2025-26/100 is rbi:circ:2025:0100."""
    number = parse_rbi_number(rbi_number)
    if number is None:
        raise IdentityError(f"not an RBI number: {rbi_number!r}")
    return make_doc_base(CORPUS, CIRCULAR, number.financial_year, number.serial)


# --------------------------------------------------------------------------- versions
#
# Decided 19 November 2026: versions follow RBI's own history. A direction's version history
# page lists its earlier consolidated texts, "Updated as on <date>", oldest first; those are
# v1 to vN and the current page is v(N+1). A direction with no history has one version, v1.
# Each version after the first supersedes the one before it.


@dataclass(frozen=True, slots=True)
class DocVersion:
    direction_id: int
    version: int
    doc_id: str
    updated_as_on: date | None
    date_source: str  # "version history" | "current title" | "listing title" | "none"
    url: str
    histid: int | None  # RBI's id for an earlier version; None for the current text
    current: bool
    supersedes: str | None


def doc_id(base: str, version: int) -> str:
    candidate = f"{base}:v{version}"
    parse_doc_id(candidate)
    return candidate


def number_versions(
    ident: DirectionIdentity,
    history: list[VersionLink],
    *,
    current_url: str,
    current_updated: date | None,
    current_date_source: str,
) -> list[DocVersion]:
    """Number a direction's versions. Raises if the history is not strictly ascending, or if
    the current text is not newer than the last earlier version."""
    dates = [h.updated_as_on for h in history]
    if any(later <= earlier for earlier, later in pairwise(dates)):
        raise IdentityError(f"direction {ident.direction_id}: version dates not ascending {dates}")
    if history and current_updated is not None and current_updated <= dates[-1]:
        raise IdentityError(
            f"direction {ident.direction_id}: current text dated {current_updated} is not newer"
            f" than its last earlier version, {dates[-1]}"
        )
    versions: list[DocVersion] = []
    previous: str | None = None
    for number, link in enumerate(history, 1):
        this = doc_id(ident.doc_base, number)
        versions.append(
            DocVersion(
                ident.direction_id, number, this, link.updated_as_on, "version history",
                link.url, link.histid, False, previous,
            )
        )  # fmt: skip
        previous = this
    number = len(history) + 1
    versions.append(
        DocVersion(
            ident.direction_id, number, doc_id(ident.doc_base, number), current_updated,
            current_date_source if current_updated is not None else "none", current_url, None,
            True, previous,
        )
    )  # fmt: skip
    return versions
