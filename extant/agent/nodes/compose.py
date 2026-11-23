"""compose: claims over the candidates, each citing them by position.

On the full path the stronger model writes the claims. It sees the candidates as a numbered
list and may name a paragraph only by its number, so the narrowest thing it can emit is an
index into a list the retrieval layer built. The resolve step turns indices into para_ids;
an index out of range fails there, as ``verification_failed``, instead of producing a claim
with no citation. ``insufficient`` is a first class outcome: it becomes
``no_support_found`` with what the candidates failed to establish.

The fast and diff paths make no model call. Their claims are templates over a deterministic
lookup, with the evidence quoted verbatim, and they go through every hard check in
``verify`` like any other claim.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import ValidationError

from extant.agent import prompts
from extant.agent.llm import ModelOutputError
from extant.agent.models import (
    AgentState,
    Candidate,
    Caveat,
    CheckResult,
    ComposerOutput,
    DraftClaim,
    RefusalReason,
)
from extant.agent.runtime import Given, Spend, as_of, parsed
from extant.core.identity import ref_variants
from extant.guards.errors import ModelUnavailableError

FAST_CLAIMS = 3
# Rung 3 of the degradation ladder: the composer failed after its retry. The retrieved
# provisions are shown whole, with no prose drawn from them.
RETRIEVAL_ONLY: dict[str, Any] = {
    "retrieval_only": True,
    "degraded": ["retrieval_only"],
    "caveats": [Caveat.DEGRADED_RETRIEVAL],
}


def _quoted_mention(body: str, canonical: str) -> str | None:
    """The reference exactly as the paragraph prints it, found without regard to case."""
    lowered = body.lower()
    for variant in ref_variants(canonical):
        at = lowered.find(variant.lower())
        if at != -1:
            return body[at : at + len(variant)]
    return None


def _paragraph(c: Candidate) -> str:
    """How a template names a paragraph. Never the para_id: that is an identifier for the
    citation, not prose, and its digits would be read as quantities by the numeric check."""
    return f"paragraph {c.paragraph_number or c.para_id.partition('#')[2]}"


def _fast(state: AgentState) -> list[DraftClaim]:
    claims = []
    for c in state.candidates:
        for r in state.references:
            hit = _quoted_mention(c.body, r.canonical_ref)
            if hit is None:
                continue
            where = f"{_paragraph(c)} of {c.doc_title}"
            if c.doc_id in r.consolidated_into:
                text = (
                    f"Circular {r.surface} was consolidated into {c.doc_title}, as {where} records."
                )
            else:
                text = f"Circular {r.surface} is referred to in {where}."
            claims.append(
                DraftClaim(text=text, citation_ids=(c.para_id,), quotes=(hit,), templated=True)
            )
            break
        if len(claims) == FAST_CLAIMS:
            break
    return claims


def _in_force_on(c: Candidate, day: date) -> bool:
    return c.valid_from <= day and (c.valid_to is None or c.valid_to > day)


def _diff(state: AgentState) -> list[DraftClaim]:
    ordered: list[Candidate] = sorted(state.candidates, key=lambda c: c.asked_for)
    if not ordered:
        return []
    if len(ordered) == 1:
        # One version found. If its own validity covers the second date, the provision did
        # not change between the two dates, which is an answer; otherwise nothing was found
        # in force on the second date.
        c = ordered[0]
        if state.compare_as_of is None or not _in_force_on(c, state.compare_as_of):
            return []
        return [
            DraftClaim(
                text=f"The same version of {_paragraph(c)} of {c.doc_title} was in force on"
                " both dates asked about, and read as quoted.",
                citation_ids=(c.para_id,),
                quotes=(c.body,),
                templated=True,
            )
        ]
    claims = []
    for label, c in zip(("earlier", "later"), ordered[:2], strict=True):
        where = _paragraph(c)
        claims.append(
            DraftClaim(
                text=f"In the version in force on the {label} date asked about, {where} of"
                f" {c.doc_title} read as quoted.",
                citation_ids=(c.para_id,),
                quotes=(c.body,),
                templated=True,
            )
        )
    return claims


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    if state.path == "fast":
        claims = _fast(state)
        if not claims:
            return {"refusal_reason": RefusalReason.NO_SUPPORT_FOUND}
        return {"draft_claims": claims}
    if state.path == "diff":
        claims = _diff(state)
        if not claims:
            return {
                "refusal_reason": RefusalReason.NO_SUPPORT_FOUND,
                "refusal_detail": "the paragraph found for the first date has no version in"
                " force on the second"
                if state.candidates
                else "no paragraph was found in force on the first date",
            }
        return {"draft_claims": claims}
    if state.retrieval_only:
        return {}  # parse already degraded: nothing is composed
    if given.model is None:
        raise RuntimeError("compose needs a model on the full path")
    shown = list(state.candidates)
    user = prompts.compose_user(state.raw_query, state.entity_type, as_of(state), shown)
    try:
        done = spend.complete(given.model, ComposerOutput, system=prompts.COMPOSE_SYSTEM, user=user)
    except ModelUnavailableError:
        return RETRIEVAL_ONLY
    except (ModelOutputError, ValidationError) as exc:
        return {
            "checks": {
                "composer_output": CheckResult(
                    name="composer_output", passed=False, detail=str(exc)[:300]
                )
            },
            "refusal_reason": RefusalReason.VERIFICATION_FAILED,
            "refusal_detail": "the composed answer could not be read",
        }
    out = parsed(done, ComposerOutput)
    if out.insufficient or not out.claims:
        return {
            "missing": out.missing,
            "refusal_reason": RefusalReason.NO_SUPPORT_FOUND,
            "refusal_detail": out.missing,
        }
    resolved, bad = resolve(out, shown)
    if bad is not None:
        return {
            "checks": {"index_in_range": bad},
            "refusal_reason": RefusalReason.VERIFICATION_FAILED,
            "refusal_detail": "a citation pointed at a paragraph that was not retrieved",
        }
    return {
        "draft_claims": resolved,
        "checks": {"index_in_range": CheckResult(name="index_in_range", passed=True)},
    }


def resolve(
    out: ComposerOutput, shown: list[Candidate]
) -> tuple[list[DraftClaim], CheckResult | None]:
    """Turn the composer's indices into para_ids. An index out of range fails here."""
    resolved = []
    for n, claim in enumerate(out.claims):
        bad = [i for i in claim.candidate_indices if not 0 <= i < len(shown)]
        if bad:
            return [], CheckResult(
                name="index_in_range",
                passed=False,
                detail=f"claim {n} cites {bad}; only 0 to {len(shown) - 1} were shown",
            )
        ids = tuple(dict.fromkeys(shown[i].para_id for i in claim.candidate_indices))
        resolved.append(DraftClaim(text=claim.text, citation_ids=ids, quotes=tuple(claim.quotes)))
    return resolved, None
