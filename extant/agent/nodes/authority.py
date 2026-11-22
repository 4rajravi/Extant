"""authority: binding force and entity agreement. Deterministic.

Material with no legal force (an FAQ, a press release) cannot carry an answer alone: when it
is all that survived the gate, it is set aside and the question is refused with
``non_binding_support_only``. Beside binding support it stays, and ``finalize`` attaches the
``non_binding_cited`` caveat if it ends up cited.

When the caller gave no entity type, the top candidates are compared. If one entity type is
named by all of them, the answer stands and each citation carries the scope it applies to.
If they disagree, the question is refused with ``entity_required``: a provision that differs
by entity type is exactly where a wrong assumption does damage.
"""

from __future__ import annotations

from typing import Any

from extant.agent.models import AgentState, Dropped, RefusalReason
from extant.agent.runtime import Given, Spend

TOP = 5  # how many ranked candidates must agree on entity scope


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    binding = [c for c in state.candidates if c.binding]
    if not binding:
        return {
            "candidates": [],
            "dropped": [
                Dropped(
                    para_id=c.para_id,
                    reason="non_binding_only",
                    detail=f"authority rank {c.authority_rank} has no legal force",
                )
                for c in state.candidates
            ],
            "set_aside": list(state.candidates),
            "refusal_reason": RefusalReason.NON_BINDING_SUPPORT_ONLY,
        }
    if state.entity_type is None and state.path == "full":
        top = [c for c in state.candidates if c.rank is not None][:TOP]
        shared = set.intersection(*(set(c.entity_scope) for c in top)) if top else set()
        scopes = {c.entity_scope for c in top}
        if len(scopes) > 1 and not shared:
            listed = "; ".join(", ".join(s) for s in sorted(scopes))
            return {"refusal_reason": RefusalReason.ENTITY_REQUIRED, "refusal_detail": listed}
    return {}
