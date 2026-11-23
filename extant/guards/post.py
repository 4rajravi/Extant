"""Post checks: pure functions over a state, called from two places with two failure modes.

A check that lives inside a node protects only the paths that call that node. These are
functions of the state alone, so they can be run again over the final state, where they
catch a state that reached a terminal node without passing through ``verify``.

- ``verify`` calls ``claim_verdicts`` and turns a failure into ``verification_failed``, with
  the failing check named: a user facing outcome.
- ``finalize`` calls ``assert_answer`` (or ``assert_provisions``) over the final state and
  raises ``PostCheckError`` if anything fails. Reaching ``finalize`` with a failing check
  means a path bypassed verification, which is a bug, never something to show a user.

Same functions, two call sites. This is the defence in depth the injection argument in the
README rests on: whatever a model emits, these run over what it emitted, and no model output
can skip them, because no model output decides whether they run.

The checks, per claim: plain text (no internal label in the prose), citation existence,
quote match (a literal substring of the cited body), supersession (nothing cited out of
force), numerics, and groundedness (content word overlap at or above the threshold, or a
judge pass on record). Per answer: the verified claims are exactly what the checks produce
from the drafts, and at least one cited paragraph is binding.
"""

from __future__ import annotations

from extant.agent.arms import BASELINE, Arm
from extant.agent.checks import OVERLAP_THRESHOLD, ClaimVerdict, verify_claim
from extant.agent.models import AgentState, Candidate, CheckResult
from extant.agent.runtime import InvariantError


class PostCheckError(InvariantError):
    """A terminal state failed a post check: a path bypassed verification."""

    def __init__(self, failures: list[CheckResult]) -> None:
        self.failures = failures
        listed = "; ".join(f"{f.name}: {f.detail}" for f in failures)
        super().__init__(f"post checks failed on the final state: {listed}")


ALL_CHECKS = frozenset({"plain_text", "citation_exists", "quote_match", "supersession", "numerics"})


def skipped(arm: Arm) -> frozenset[str]:
    """The claim checks an ablation arm turns off. Serving's arm turns off none."""
    if not arm.verification:
        return ALL_CHECKS
    return frozenset() if arm.supersession else frozenset({"supersession"})


def claim_verdicts(state: AgentState, arm: Arm = BASELINE) -> list[ClaimVerdict]:
    """The hard checks on every draft claim, against the candidates in the state."""
    skip = skipped(arm)
    return [
        verify_claim(i, c, state.candidates, skip=skip) for i, c in enumerate(state.draft_claims)
    ]


def _groundedness(state: AgentState, verdicts: list[ClaimVerdict]) -> list[CheckResult]:
    out = []
    for v in verdicts:
        if v.overlap >= OVERLAP_THRESHOLD or state.draft_claims[v.index].templated:
            passed, detail = True, f"overlap {v.overlap:.2f}"
        else:
            judged = state.checks.get(f"claim{v.index}.judge")
            passed = judged is not None and judged.passed
            detail = f"overlap {v.overlap:.2f}, " + (
                "judge found it supported" if passed else "no judge pass on record"
            )
        out.append(CheckResult(name=f"claim{v.index}.groundedness", passed=passed, detail=detail))
    return out


def answer_checks(state: AgentState, arm: Arm = BASELINE) -> list[CheckResult]:
    """Every post check an answer must pass, run over the state as it stands."""
    if not state.draft_claims:
        return [CheckResult(name="claims", passed=False, detail="an answer with no draft claims")]
    verdicts = claim_verdicts(state, arm)
    results = [
        r.model_copy(update={"name": f"claim{v.index}.{name}"})
        for v in verdicts
        for name, r in v.checks.items()
    ]
    results += _groundedness(state, verdicts)
    expected = [v.claim for v in verdicts if v.claim is not None]
    results.append(
        CheckResult(
            name="verified_claims",
            passed=state.verified_claims == expected and len(expected) == len(verdicts),
            detail="" if state.verified_claims == expected else "differ from what the checks give",
        )
    )
    by_id = {c.para_id: c for c in state.candidates}
    cited = [
        by_id[cit.para_id]
        for claim in state.verified_claims
        for cit in claim.citations
        if cit.para_id in by_id
    ]
    results.append(
        CheckResult(
            name="authority",
            passed=any(c.binding for c in cited),
            detail="" if any(c.binding for c in cited) else "no binding paragraph is cited",
        )
    )
    return results


def provision_checks(state: AgentState, provisions: list[Candidate]) -> list[CheckResult]:
    """A retrieval only response: every provision is a candidate, in force, quoted whole."""
    allowed = {c.para_id for c in state.candidates}
    results = []
    for c in provisions:
        ok = c.para_id in allowed and c.in_force() and bool(c.body)
        results.append(
            CheckResult(
                name=f"provision.{c.para_id}",
                passed=ok,
                detail="" if ok else "not a candidate in force on the date asked",
            )
        )
    if not provisions:
        results.append(CheckResult(name="provisions", passed=False, detail="none to show"))
    return results


def _raise_on_failure(results: list[CheckResult]) -> None:
    failed = [r for r in results if not r.passed]
    if failed:
        raise PostCheckError(failed)


def assert_answer(state: AgentState, arm: Arm = BASELINE) -> None:
    if not arm.verification:
        return  # the ablation arm with no verification: nothing between composer and answer
    _raise_on_failure(answer_checks(state, arm))


def assert_provisions(state: AgentState, provisions: list[Candidate]) -> None:
    _raise_on_failure(provision_checks(state, provisions))
