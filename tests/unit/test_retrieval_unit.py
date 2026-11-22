"""The retrieval library's pure parts: arm ranking, fusion, reference detection, boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from extant.retrieval import TsRankCd, _find_references, _rank_arms, _rrf, search_sql
from tests.unit.test_rbi_boundaries import MODEL_CLIENTS, _hits, imported_modules

RETRIEVAL = Path(__file__).resolve().parents[2] / "extant" / "retrieval.py"


def test_each_arm_is_ranked_in_its_own_direction() -> None:
    rows = [
        ("dense", 1, 0.40),
        ("dense", 2, 0.10),
        ("dense", 3, 0.25),
        ("lexical", 4, 0.2),
        ("lexical", 5, 0.9),
        ("lexical", 2, 0.5),
    ]
    arms = _rank_arms(rows)
    assert arms["dense"] == [2, 3, 1]  # smallest distance first
    assert arms["lexical"] == [5, 2, 4]  # largest score first


def test_reversing_either_arm_changes_the_fusion() -> None:
    rows = [("dense", 1, 0.1), ("dense", 2, 0.9), ("lexical", 1, 0.9), ("lexical", 2, 0.1)]
    good = _rrf(list(_rank_arms(rows).values()), (1.0, 1.0))
    assert good[0][0] == 1
    # The obvious bug: sorting lexical ascending like the dense arm would favour chunk 2.
    wrong = _rrf([[1, 2], [2, 1]], (1.0, 1.0))
    assert wrong[0][1] == wrong[1][1]


def test_rrf_matches_the_formula_and_honours_weights() -> None:
    fused = dict(_rrf([[10, 20], [20, 30]], (1.0, 2.0), k=60))
    assert fused[10] == pytest.approx(1 / 61)
    assert fused[20] == pytest.approx(1 / 62 + 2 / 61)
    assert fused[30] == pytest.approx(2 / 62)
    assert [cid for cid, _ in _rrf([[10, 20], [20, 30]], (1.0, 2.0))] == [20, 30, 10]


def test_rrf_breaks_ties_on_chunk_id() -> None:
    assert [cid for cid, _ in _rrf([[7], [3]], (1.0, 1.0))] == [3, 7]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "Is DBOD.No.BP.BC.12/21.04.048/2014-15 still in force?",
            "DBOD.BP.BC.12/21.04.048/2014-15",
        ),
        ("what replaced DBOD.No. BP.BC.12/21.04.048/2014-15", "DBOD.BP.BC.12/21.04.048/2014-15"),
        ("circular RBI/2025-26/100 withdrew what", "RBI/2025-26/100"),
        ("see DNBS.213/CGM(ASR)-2010.", "DNBS.213/CGM(ASR)-2010"),
    ],
)
def test_a_literal_reference_is_found_and_normalised(query: str, expected: str) -> None:
    assert [c for _, c in _find_references(query)] == [expected]


@pytest.mark.parametrize(
    "query",
    [
        "dividend payout limits for 2025-26",
        "and/or the board shall decide",
        "available 24/7 to customers",
        "Rs.5/- per transaction",
        "what does paragraph 4.3A say",
    ],
)
def test_ordinary_text_is_not_a_reference(query: str) -> None:
    assert _find_references(query) == []


def test_the_search_filters_into_a_materialised_set_before_either_limit() -> None:
    statement = search_sql(TsRankCd())
    allowed = statement.index("allowed AS MATERIALIZED")
    assert allowed < statement.index("LIMIT")
    assert statement.index("entity_codes &&") < statement.index("LIMIT")
    assert "FROM allowed ORDER BY dist ASC" in statement
    assert "ORDER BY score DESC" in statement
    assert "valid_to IS NULL OR valid_to > %(as_of)s" in statement


def test_retrieval_imports_no_model_or_http_client() -> None:
    modules = imported_modules(RETRIEVAL)
    assert not _hits(modules, MODEL_CLIENTS)
    assert not _hits(modules, {"httpx", "requests", "urllib", "aiohttp"})
