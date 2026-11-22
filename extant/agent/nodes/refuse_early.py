"""refuse_early: the two routes that end before any retrieval. Deterministic."""

from __future__ import annotations

from typing import Any

from extant.agent.models import AgentState, RefusalReason
from extant.agent.runtime import Given, Spend

_REASON = {
    "out_of_scope": RefusalReason.OUT_OF_SCOPE,
    "refuse": RefusalReason.COMPUTATION_REQUESTED,
}


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    if state.path not in _REASON:
        raise RuntimeError(f"refuse_early reached on path {state.path!r}")
    return {"outcome": "refusal", "refusal_reason": _REASON[state.path]}
