"""resolve_reference: the fast path's lookup, one of the two nodes that read the database.

A literal reference in the question is normalised by ``canonical_ref``, looked up in the
withdrawal records and the concordance, and found as a mention inside paragraph text through
``ref_variants``. Mentions are fetched across every version, so the gate can drop the ones
out of force on the record. No model and no embedding are called: this path costs nothing
and cannot invent a match.
"""

from __future__ import annotations

from typing import Any

from extant.agent.models import AgentState
from extant.agent.runtime import Given, Spend, as_of


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    index = given.index
    if index is None:
        raise RuntimeError("resolve_reference needs the index")
    codes = (state.entity_type,) if state.entity_type else index.entity_codes()
    candidates, references = index.reference(state.raw_query, codes, as_of(state))
    return {"candidates": candidates, "references": references}
