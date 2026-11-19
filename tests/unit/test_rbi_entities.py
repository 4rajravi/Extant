"""The entity taxonomy. Sentences are real ones from the NBFC directions."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from extant.core.models import EntityType
from extant.corpora.rbi import sources
from extant.corpora.rbi.entities import (
    CATEGORIES,
    NBFC_SUBTYPES,
    TaxonomyError,
    category_for_listing,
    check_listing_labels,
    entity_types,
    subtype_evidence,
    subtypes_in,
)

LISTING = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "rbi"
    / "listing_did404_small_finance_banks.html"
)


def test_eleven_categories_with_the_codes_decided() -> None:
    assert [(c.code, c.label) for c in CATEGORIES] == [
        ("cb", "Commercial Banks"),
        ("sfb", "Small Finance Banks"),
        ("pb", "Payments Banks"),
        ("lab", "Local Area Banks"),
        ("rrb", "Regional Rural Banks"),
        ("ucb", "Urban Co-operative Banks"),
        ("rcb", "Rural Co-operative Banks"),
        ("aifi", "All India Financial Institutions"),
        ("nbfc", "Non-Banking Financial Companies"),
        ("arc", "Asset Reconstruction Companies"),
        ("cic", "Credit Information Companies"),
    ]
    assert category_for_listing(404).code == category_for_listing(420).code == "sfb"


def test_entity_types_are_valid_models_with_unique_codes() -> None:
    types = entity_types()
    assert all(isinstance(t, EntityType) for t in types)
    assert len(types) == 11 + len(NBFC_SUBTYPES) == 28
    assert all(t.code.startswith("nbfc_") for t in types[11:])
    assert "nbfc_cic" in {t.code for t in types}


def test_the_real_listing_labels_match() -> None:
    rows = sources.parse_listing(LISTING.read_bytes(), department="regulation", listing_id=404)
    check_listing_labels(rows)


def test_a_renamed_category_fails_loudly() -> None:
    row = sources.ListingRow(
        "regulation", 404, "Small Finance Bank", date(2025, 11, 28), "t", 1, None, None
    )
    with pytest.raises(TaxonomyError, match="taxonomy says"):
        check_listing_labels([row])
    with pytest.raises(TaxonomyError, match="not one of the 11"):
        category_for_listing(999)


@pytest.mark.parametrize(
    ("sentence", "codes"),
    [
        ("(c) NBFC-Factor registered with the RBI under the provisions of the Factoring Regulation"
         " Act, 2011;", {"nbfc_factor"}),
        ("NBFCs in the Middle Layer (NBFC-ML) or above layers shall comply", {"nbfc_ml"}),
        ("An NBFC-UL shall be mandatorily listed within three years", {"nbfc_ul"}),
        ("(f) IDF-NBFC registered with the RBI", {"nbfc_idf"}),
        ("These Directions shall apply to every Core Investment Company (hereinafter collectively"
         " referred to as 'CICs')", {"nbfc_cic"}),
        ("shall be applicable to HFC registered with the RBI", {"nbfc_hfc"}),
        ("shall be applicable to SPD registered with the RBI as NBFC", {"nbfc_spd"}),
        # a bare CICs is Credit Information Companies here, not Core Investment Companies
        ("Chapter-II Membership of CICs", set()),
        # retired categories are not subtypes; NBFC-D in the same sentence is
        ("A.6 References to NBFC-ND, NBFC-ND-SI and NBFC-D", {"nbfc_d"}),
        # lending service providers are not a kind of NBFC
        ("A. General Requirements for NBFC-LSP Arrangements", set()),
    ],
)  # fmt: skip
def test_subtypes_named_in_real_sentences(sentence: str, codes: set[str]) -> None:
    assert subtypes_in(sentence) == codes


def test_subtype_evidence_counts_directions_and_occurrences() -> None:
    evidence = {
        e.code: e
        for e in subtype_evidence({12949: "NBFC-D and NBFC-D again", 12950: "NBFC-ICC, NBFC-D"})
    }
    assert (evidence["nbfc_d"].directions, evidence["nbfc_d"].occurrences) == (2, 3)
    assert evidence["nbfc_d"].first_direction_id == 12949
    assert (evidence["nbfc_cic"].directions, evidence["nbfc_cic"].first_direction_id) == (0, None)
