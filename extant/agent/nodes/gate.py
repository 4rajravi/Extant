"""gate: the supersession gate. Deterministic.

Nothing out of force on the date it was asked for survives, and every removal is recorded in
``dropped`` with its reason: superseded (its validity ended on or before the date),
not yet effective (it starts after the date), or out of entity scope (it names none of the
entity types asked about). After this node runs, candidates may only shrink.

An empty set afterwards is a refusal: ``superseded_only`` when what was found was all out of
force (including a circular the question names that RBI withdrew on or before the date), and
``no_support_found`` otherwise.
"""

from __future__ import annotations

from typing import Any

from extant.agent.models import AgentState, Candidate, Dropped, RefusalReason
from extant.agent.runtime import Given, Spend, as_of


def judge(c: Candidate, entity: str | None, *, dates: bool = True) -> Dropped | None:
    """The reason ``c`` cannot stand, or None. ``dates=False`` is the Phase 6 ablation arm
    without supersession handling: entity scope still applies, dates do not."""
    on = c.asked_for
    if dates and c.valid_to is not None and c.valid_to <= on:
        return Dropped(
            para_id=c.para_id,
            reason="superseded",
            detail=f"in force {c.valid_from} to {c.valid_to}, asked for {on}",
        )
    if dates and c.valid_from > on:
        return Dropped(
            para_id=c.para_id,
            reason="not_yet_effective",
            detail=f"in force from {c.valid_from}, asked for {on}",
        )
    if entity is not None and entity not in c.entity_scope:
        return Dropped(
            para_id=c.para_id,
            reason="out_of_entity_scope",
            detail=f"applies to {', '.join(c.entity_scope)}, asked for {entity}",
        )
    return None


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    keep: list[Candidate] = []
    dropped: list[Dropped] = []
    set_aside: list[Candidate] = []
    for c in state.candidates:
        why = judge(c, state.entity_type, dates=given.arm.supersession)
        if why is None:
            keep.append(c)
        else:
            dropped.append(why)
            set_aside.append(c)
    update: dict[str, Any] = {
        "candidates": keep,
        "dropped": dropped,
        "set_aside": set_aside,
        "gate_ran": True,
    }
    if keep:
        return update
    on = as_of(state)
    withdrawn = any(w.effective_on <= on for r in state.references for w in r.withdrawals)
    superseded = any(d.reason in ("superseded", "not_yet_effective") for d in dropped)
    if withdrawn or superseded:
        update["refusal_reason"] = RefusalReason.SUPERSEDED_ONLY
    else:
        update["refusal_reason"] = RefusalReason.NO_SUPPORT_FOUND
    return update
