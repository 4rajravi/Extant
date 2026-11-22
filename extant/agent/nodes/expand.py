"""expand: admit each ranked result's ordinal neighbours as unscored context. Deterministic.

The neighbours were fetched by ``retrieve`` (the only node that reads the database); this
node marks them expanded and adds them to the candidates. An expanded row carries no fusion
score and no rank, so it can be cited but never outranks what retrieval actually found."""

from __future__ import annotations

from typing import Any

from extant.agent.models import AgentState
from extant.agent.runtime import Given, Spend


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    have = {c.para_id for c in state.candidates}
    added = []
    for n in state.neighbours:
        if n.para_id in have:
            continue
        have.add(n.para_id)
        added.append(
            n.model_copy(
                update={"expanded": True, "origin": "expanded", "fusion_score": None, "rank": None}
            )
        )
    return {"candidates": [*state.candidates, *added], "neighbours": []}
