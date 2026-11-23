"""verify: the deterministic checks on every claim, and the first groundedness pass.

The spec's four checks are citation existence, quote match, supersession and numerics; a
fifth, plain text, refuses a claim whose prose carries an internal label (the composer's
"[3]", or a para_id), which the asker would otherwise read. Any failure routes the request
to ``verification_failed``; it never becomes an answer. A claim that passes every check but
whose content words overlap its quotes below the threshold is residual: it goes to the
judge. Claims written by code (the fast and diff paths) are never residual.
"""

from __future__ import annotations

from typing import Any

from extant.agent.checks import OVERLAP_THRESHOLD
from extant.agent.models import AgentState, CheckResult, RefusalReason
from extant.agent.runtime import Given, Spend
from extant.guards import post

_PLAIN = {
    "plain_text": "the wording of a claim",
    "citation_exists": "a citation",
    "quote_match": "a quote",
    "supersession": "the date of a cited paragraph",
    "numerics": "a number",
}


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    if not state.draft_claims:
        return {"refusal_reason": RefusalReason.NO_SUPPORT_FOUND}
    checks: dict[str, CheckResult] = dict(state.checks)
    verdicts = post.claim_verdicts(state)  # the post checks, hoisted; finalize runs them again
    first_failure: str | None = None
    for v in verdicts:
        for name, result in v.checks.items():
            checks[f"claim{v.index}.{name}"] = result
            if not result.passed and first_failure is None:
                first_failure = name
        checks[f"claim{v.index}.overlap"] = CheckResult(
            name="overlap", passed=v.overlap >= OVERLAP_THRESHOLD, detail=f"{v.overlap:.2f}"
        )
    if first_failure is not None:
        return {
            "checks": checks,
            "refusal_reason": RefusalReason.VERIFICATION_FAILED,
            "refusal_detail": _PLAIN.get(first_failure, first_failure),
        }
    residual = [
        v.index
        for v in verdicts
        if v.overlap < OVERLAP_THRESHOLD and not state.draft_claims[v.index].templated
    ]
    claims = [v.claim for v in verdicts if v.claim is not None]
    return {"verified_claims": claims, "checks": checks, "residual": residual}
