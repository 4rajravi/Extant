"""judge: a narrow second opinion on residual claims, from the cheap tier.

It sees one claim and the paragraphs that claim cites, and nothing else: not the question,
not the other candidates, not the composer's output. That is what keeps its verdict
independent of the composer's reasoning. A claim judged unsupported refuses the request.
With no residual claim the node runs and calls nothing, and its zero row says so.

Any metric that uses the judge is reported with the judge's agreement rate against hand
labels (``evals/judge-labels/``), which Phase 6 computes.
"""

from __future__ import annotations

from typing import Any

from extant.agent import prompts
from extant.agent.models import AgentState, CheckResult, JudgeOutput, RefusalReason
from extant.agent.runtime import Given, Spend, parsed


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    if not state.residual:
        return {}
    if given.model is None:
        raise RuntimeError("judge needs a model for residual claims")
    by_id = {c.para_id: c for c in state.candidates}
    checks = dict(state.checks)
    unsupported = []
    for i in state.residual:
        claim = state.draft_claims[i]
        bodies = [by_id[p].body for p in claim.citation_ids]
        done = spend.complete(
            given.model,
            JudgeOutput,
            system=prompts.JUDGE_SYSTEM,
            user=prompts.judge_user(claim.text, bodies),
        )
        out = parsed(done, JudgeOutput)
        checks[f"claim{i}.judge"] = CheckResult(name="judge", passed=out.supported)
        if not out.supported:
            unsupported.append(i)
    if unsupported:
        return {
            "checks": checks,
            "refusal_reason": RefusalReason.VERIFICATION_FAILED,
            "refusal_detail": "a claim the judge did not find supported by its paragraphs",
        }
    return {"checks": checks}
