"""The RBI corpus's entity taxonomy: who a direction applies to, as entity codes.

Decided 19 November 2026. Two levels, both taken from RBI's own pages, never from general
knowledge of Indian banking:

* The 11 regulated entity categories, labelled verbatim as the Master Direction listings label
  them, with one short code each. ``cic`` is Credit Information Companies, the listing's category.
* Subtypes of Non-Banking Financial Companies, which only the text of the NBFC directions names,
  all ``nbfc_*``. Core Investment Companies are ``nbfc_cic``, so that ``cic`` is never ambiguous.
  Each label is the long form the directions themselves print. Three tokens seen in the text are
  not subtypes and are excluded: NBFC-ND and NBFC-ND-SI are retired categories ("From October 01,
  2022, all references to NBFC-ND, NBFC-ND-SI and NBFC-D ..."), and NBFC-LSP appears only as
  "NBFC-LSP Arrangements", lending service providers rather than a kind of NBFC.

The patterns below find a subtype in a direction's text, for evidence counts; deciding which
entities a given paragraph applies to is the parser's job.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from extant.core.models import EntityType
from extant.corpora.rbi.sources import ListingRow


@dataclass(frozen=True, slots=True)
class Category:
    code: str
    label: str  # verbatim from the listing's header row
    regulation_listing_id: int
    supervision_listing_id: int


CATEGORIES: tuple[Category, ...] = (
    Category("cb", "Commercial Banks", 403, 419),
    Category("sfb", "Small Finance Banks", 404, 420),
    Category("pb", "Payments Banks", 405, 421),
    Category("lab", "Local Area Banks", 406, 422),
    Category("rrb", "Regional Rural Banks", 407, 423),
    Category("ucb", "Urban Co-operative Banks", 408, 424),
    Category("rcb", "Rural Co-operative Banks", 409, 425),
    Category("aifi", "All India Financial Institutions", 410, 426),
    Category("nbfc", "Non-Banking Financial Companies", 411, 427),
    Category("arc", "Asset Reconstruction Companies", 412, 428),
    Category("cic", "Credit Information Companies", 413, 429),
)
_BY_LISTING = {
    lid: cat
    for cat in CATEGORIES
    for lid in (cat.regulation_listing_id, cat.supervision_listing_id)
}


@dataclass(frozen=True, slots=True)
class Subtype:
    code: str
    label: str  # the long form the NBFC directions print
    pattern: re.Pattern[str]


def _p(*alternatives: str) -> re.Pattern[str]:
    return re.compile("|".join(rf"(?<![A-Za-z-]){a}(?![A-Za-z-])" for a in alternatives))


NBFC = "Non-Banking Financial Companies"
NBFC_SUBTYPES: tuple[Subtype, ...] = (
    Subtype("nbfc_bl", "NBFC-Base Layer", _p(r"NBFC\s?-\s?BL", r"Base Layer")),
    Subtype("nbfc_ml", "NBFC-Middle Layer", _p(r"NBFC\s?-\s?ML", r"Middle Layer")),
    Subtype("nbfc_ul", "NBFC - Upper Layer", _p(r"NBFC\s?-\s?UL", r"Upper Layer")),
    Subtype(
        "nbfc_tl", "NBFC-TL, NBFCs falling in the Top Layer", _p(r"NBFC\s?-\s?TL", r"Top Layer")
    ),
    Subtype("nbfc_d", "Deposit taking NBFC", _p(r"NBFC\s?-\s?D", r"Deposit[- ]taking NBFCs?")),
    Subtype(
        "nbfc_icc",
        "NBFC-Investment and Credit Company",
        _p(r"NBFC\s?-\s?ICCs?", r"Investment and Credit Compan(?:y|ies)"),
    ),
    Subtype(
        "nbfc_mfi",
        "Non-Banking Financial Company \u2013 Microfinance Institution",
        _p(r"NBFC\s?-\s?MFIs?", r"Micro ?[Ff]inance Institutions?"),
    ),
    Subtype("nbfc_factor", "NBFC-Factor", _p(r"NBFC\s?-\s?Factors?")),
    Subtype(
        "nbfc_ifc",
        "Non-Banking Financial Company-Infrastructure Finance Company",
        _p(r"NBFC\s?-\s?IFCs?", r"Infrastructure Finance Compan(?:y|ies)"),
    ),
    Subtype(
        "nbfc_idf",
        "Infrastructure Debt Fund-Non-Banking Financial Company",
        _p(r"IDF\s?-\s?NBFCs?", r"Infrastructure Debt Funds?"),
    ),
    Subtype(
        "nbfc_p2p",
        "NBFC-Peer to Peer Lending Platform",
        _p(r"NBFC\s?-\s?P2Ps?", r"Peer to Peer Lending Platforms?"),
    ),
    Subtype("nbfc_aa", "NBFC-Account Aggregator", _p(r"NBFC\s?-\s?AAs?", r"Account Aggregators?")),
    # Never a bare "CIC": in NBFC directions that can mean Credit Information Companies.
    Subtype(
        "nbfc_cic",
        "NBFC-Core Investment Company",
        _p(r"NBFC\s?-\s?CICs?", r"Core Investment Compan(?:y|ies)"),
    ),
    Subtype(
        "nbfc_hfc", "Housing Finance Company", _p(r"HFCs?", r"Housing Finance Compan(?:y|ies)")
    ),
    Subtype(
        "nbfc_mgc",
        "Mortgage Guarantee Company",
        _p(r"MGCs?", r"Mortgage Guarantee Compan(?:y|ies)"),
    ),
    Subtype("nbfc_spd", "Standalone Primary Dealer", _p(r"SPDs?", r"Standalone Primary Dealers?")),
    Subtype(
        "nbfc_nofhc",
        "Non-Operative Financial Holding Company",
        _p(r"NOFHCs?", r"Non-Operative Financial Holding Compan(?:y|ies)"),
    ),
)
EXCLUDED_TOKENS = {
    "NBFC-ND": "retired category, read under the scale based framework from October 01, 2022",
    "NBFC-ND-SI": "retired category, read under the scale based framework from October 01, 2022",
    "NBFC-LSP": "appears only as 'NBFC-LSP Arrangements', lending service providers",
}


class TaxonomyError(ValueError):
    """The listings no longer match the taxonomy, so codes would be assigned wrongly."""


def category_for_listing(listing_id: int) -> Category:
    try:
        return _BY_LISTING[listing_id]
    except KeyError:
        raise TaxonomyError(f"listing {listing_id} is not one of the 11 categories") from None


def check_listing_labels(rows: Iterable[ListingRow]) -> None:
    """Raise if any listing's header differs from the label its code was given for. A renamed
    category must fail loudly rather than quietly keep an old code."""
    for row in rows:
        expected = category_for_listing(row.listing_id).label
        if row.category != expected:
            raise TaxonomyError(
                f"listing {row.listing_id} is headed {row.category!r},"
                f" the taxonomy says {expected!r}"
            )


def entity_types() -> list[EntityType]:
    """Every entity type, as the Phase 0 model: the 11 categories, then the NBFC subtypes."""
    types = [EntityType(code=c.code, label=c.label) for c in CATEGORIES]
    types += [EntityType(code=s.code, label=s.label) for s in NBFC_SUBTYPES]
    codes = [t.code for t in types]
    if len(set(codes)) != len(codes):
        raise TaxonomyError("an entity code is used twice")
    return types


def subtypes_in(text: str) -> set[str]:
    """NBFC subtype codes a text mentions."""
    return {s.code for s in NBFC_SUBTYPES if s.pattern.search(text)}


@dataclass(frozen=True, slots=True)
class SubtypeEvidence:
    code: str
    directions: int  # NBFC directions whose text mentions it
    occurrences: int
    first_direction_id: int | None


def subtype_evidence(texts: dict[int, str]) -> list[SubtypeEvidence]:
    """How often each subtype appears across NBFC direction texts (direction id to text)."""
    hits: dict[str, set[int]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for direction_id in sorted(texts):
        for s in NBFC_SUBTYPES:
            n = len(s.pattern.findall(texts[direction_id]))
            if n:
                hits[s.code].add(direction_id)
                counts[s.code] += n
    return [
        SubtypeEvidence(
            s.code, len(hits[s.code]), counts[s.code], min(hits[s.code]) if hits[s.code] else None
        )
        for s in NBFC_SUBTYPES
    ]
