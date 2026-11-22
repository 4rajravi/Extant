"""Hand-built eval result rows, small enough that the right metric is obvious by inspection."""

from __future__ import annotations

from datetime import date
from typing import Any


def row(
    query_id: str,
    *,
    para_rank: int | None = None,
    doc_rank: int | None = None,
    ctx_rank: int | None = None,
    gold_paras: tuple[str, ...] = ("d#p1",),
    set_name: str = "temporal",
    arm: str = "hybrid",
    split: str = "dev",
    corpus: str = "full",
    holdout: bool = False,
    returned: int = 10,
    entity_violations: int = 0,
    as_of_violations: int = 0,
) -> dict[str, Any]:
    return {
        "set": set_name,
        "arm": arm,
        "split": split,
        "corpus": corpus,
        "schema": "idx_rbi_20261120_38ede3c",
        "ablation": "baseline",
        "query_id": query_id,
        "holdout": holdout,
        "as_of": date(2026, 1, 1),
        "gold_para_ids": list(gold_paras),
        "gold_doc_ids": ["d"],
        "para_rank": para_rank,
        "doc_rank": doc_rank,
        "para_rank_with_context": ctx_rank if ctx_rank is not None else para_rank,
        "returned_count": returned,
        "entity_violations": entity_violations,
        "as_of_violations": as_of_violations,
    }
