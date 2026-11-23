"""retry: one bounded retry into the composer after a recoverable verification failure.

Eval only. Phase 4 left this out because it doubles the worst case cost of a request; Phase 6
builds it behind an ablation arm (``extant.agent.arms``) to measure whether the false refusals
it recovers are worth that. Serving never adds this node to the graph.

Recoverable means the composer can fix it by writing again: a quote it did not copy exactly,
a number it did not quote, or an internal label in its prose. A paragraph out of force or a
citation outside the candidates is not recoverable by rewording, so it is not retried. The
retry sees its own failed claims and quotes, never an expected answer. Its new claims pass
the same checks; anything still failing stays a refusal.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from extant.agent import prompts
from extant.agent.checks import OVERLAP_THRESHOLD
from extant.agent.llm import ModelOutputError
from extant.agent.models import AgentState, CheckResult, ComposerOutput, RefusalReason
from extant.agent.nodes.compose import resolve
from extant.agent.runtime import Given, Spend, as_of, parsed
from extant.guards import post
from extant.guards.errors import ModelUnavailableError

RECOVERABLE = frozenset({"quote_match", "numerics", "plain_text"})


def _failures(state: AgentState) -> tuple[list[str], bool]:
    """The failed claim checks, and whether every one of them is recoverable."""
    # The overlap row is a measurement for the judge, not a hard check: a misquoted claim
    # always has zero overlap, so counting it would make every misquote unrecoverable.
    failed = [
        (key, r)
        for key, r in state.checks.items()
        if key.startswith("claim")
        and not r.passed
        and key.count(".") == 1
        and not key.endswith(".overlap")
    ]
    names = {key.split(".", 1)[1] for key, _ in failed}
    return [f"{key}: {r.detail}" for key, r in failed], bool(failed) and names <= RECOVERABLE


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    if state.refusal_reason is not RefusalReason.VERIFICATION_FAILED or given.model is None:
        return {}
    failures, recoverable = _failures(state)
    if not recoverable:
        return {}
    shown = list(state.candidates)
    user = prompts.compose_user(state.raw_query, state.entity_type, as_of(state), shown)
    try:
        done = spend.complete(
            given.model,
            ComposerOutput,
            system=prompts.COMPOSE_SYSTEM,
            user=user + prompts.retry_note(failures),
        )
    except (ModelUnavailableError, ModelOutputError, ValidationError):
        return {}
    out = parsed(done, ComposerOutput)
    checks = dict(state.checks)
    if out.insufficient or not out.claims:
        checks["retry"] = CheckResult(name="retry", passed=False, detail="insufficient on retry")
        return {"checks": checks}
    drafts, bad = resolve(out, shown)
    if bad is not None:
        checks["retry"] = bad
        return {"checks": checks}
    verdicts = post.claim_verdicts(state.model_copy(update={"draft_claims": drafts}), given.arm)
    for v in verdicts:
        for name, r in v.checks.items():
            checks[f"retry.claim{v.index}.{name}"] = r
    if not all(v.claim is not None for v in verdicts):
        checks["retry"] = CheckResult(name="retry", passed=False, detail="still failing")
        return {"checks": checks, "refusal_detail": "a check still failed after one retry"}
    checks["retry"] = CheckResult(name="retry", passed=True)
    return {
        "draft_claims": drafts,
        "verified_claims": [v.claim for v in verdicts if v.claim is not None],
        "residual": [
            v.index
            for v in verdicts
            if v.overlap < OVERLAP_THRESHOLD and not drafts[v.index].templated
        ],
        "checks": checks,
        "refusal_reason": None,
        "refusal_detail": None,
    }
