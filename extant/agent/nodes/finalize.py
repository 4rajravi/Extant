"""finalize: the terminal state and the response. Deterministic.

An answer needs verified claims (the state validator refuses one without). Caveats attach to
answers and never replace them: two are decided here from what was actually cited,
``non_binding_cited`` (binding support with an FAQ or press release beside it) and
``amendment_pending`` (a cited paragraph is replaced by a later version with effect from a
date still in the future). ``dropped`` is populated on answers and refusals alike: it is
what lets a reader see what the supersession gate removed.

Before an answer leaves, the post checks run again over the final state
(``extant.guards.post``). ``verify`` already ran them; a failure here means a path reached
``finalize`` without passing through ``verify``, which is a bug, so it raises instead of
refusing. A retrieval only response (a model failed after its retry) is checked the same way:
every provision it shows is a candidate in force on the date asked.
"""

from __future__ import annotations

from typing import Any

from extant.agent import refusals
from extant.agent.checks import Located, citation
from extant.agent.models import (
    AgentState,
    Answer,
    AskResponse,
    Candidate,
    Caveat,
    Citation,
    Refusal,
    RefusalReason,
    RetrievalOnly,
    Retrieved,
)
from extant.agent.runtime import Given, Spend, as_of
from extant.guards import post

PROVISIONS = 5  # how many retrieved provisions a retrieval only response shows

_NEAREST = {
    RefusalReason.ENTITY_REQUIRED,
    RefusalReason.SUPERSEDED_ONLY,
    RefusalReason.NON_BINDING_SUPPORT_ONLY,
    RefusalReason.VERIFICATION_FAILED,
}


def _retrieved(c: Candidate) -> Retrieved:
    return Retrieved(
        para_id=c.para_id,
        fusion_score=c.fusion_score,
        lexical_rank=c.lexical_rank,
        dense_rank=c.dense_rank,
        expanded=c.expanded,
    )


def _nearest(state: AgentState, reason: RefusalReason) -> list[Citation]:
    if reason not in _NEAREST:
        return []
    if reason in (RefusalReason.SUPERSEDED_ONLY, RefusalReason.NON_BINDING_SUPPORT_ONLY):
        pool = list(state.set_aside)
    elif reason is RefusalReason.VERIFICATION_FAILED and state.draft_claims:
        cited = {p for c in state.draft_claims for p in c.citation_ids}
        pool = [c for c in state.candidates if c.para_id in cited]
    else:
        pool = [c for c in state.candidates if c.rank is not None]
    return [refusals.nearest_citation(c) for c in pool[:5]]


def _late_caveats(state: AgentState) -> list[Caveat]:
    cited = {cit.para_id for claim in state.verified_claims for cit in claim.citations}
    used = [c for c in state.candidates if c.para_id in cited]
    out = []
    if any(c.binding for c in used) and any(not c.binding for c in used):
        out.append(Caveat.NON_BINDING_CITED)
    if any(c.valid_to is not None and c.valid_to > state.today for c in used):
        out.append(Caveat.AMENDMENT_PENDING)
    return out


def _whole(c: Candidate) -> Citation:
    return citation(Located(c, c.body, 0, len(c.body)))


def _retrieval_only(state: AgentState) -> RetrievalOnly:
    shown = [c for c in state.candidates if c.rank is not None][:PROVISIONS]
    post.assert_provisions(state, shown)
    who = state.entity_type or "any entity type"
    return RetrievalOnly(
        message=(
            f"An answer could not be composed just now, so these are the provisions found in"
            f" force for {who} on {as_of(state).isoformat()}, quoted in full, with nothing"
            f" drawn from them. {refusals.CHECK_THE_INSTRUMENT}"
        ),
        provisions=[_whole(c) for c in shown],
        caveats=list(dict.fromkeys(state.caveats)),
    )


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    on = as_of(state)
    late: list[Caveat] = []
    result: Answer | Refusal | RetrievalOnly
    if state.refusal_reason is None and state.retrieval_only:
        result = _retrieval_only(state)
        outcome = "retrieval_only"
    elif state.refusal_reason is None:
        post.assert_answer(state, given.arm)  # raises if a path bypassed verify
        late = _late_caveats(state)
        caveats = list(dict.fromkeys([*state.caveats, *late]))
        result = Answer(claims=state.verified_claims, caveats=caveats)
        outcome = "answer"
    else:
        reason = state.refusal_reason
        groups = refusals.group_by_scope([c for c in state.candidates if c.rank is not None][:5])
        result = Refusal(
            reason=reason,
            message=refusals.message(state, reason, probe=state.set_aside, groups=groups),
            nearest=_nearest(state, reason),
        )
        outcome = "refusal"
    seen = [*state.candidates, *state.set_aside]
    response = AskResponse(
        result=result,
        as_of=on,
        entity_type=state.entity_type,
        index_version=state.index_version,
        retrieved=[_retrieved(c) for c in {c.para_id: c for c in seen}.values()],
        dropped=list(state.dropped),
        degraded=list(dict.fromkeys(state.degraded)),
        cost_eur=round(sum(n.eur for n in state.node_log), 6),
        latency_ms=round(sum(n.latency_ms for n in state.node_log)),
    )
    update: dict[str, Any] = {"response": response}
    if state.outcome is None:
        update["outcome"] = outcome
    if late:
        update["caveats"] = late
    return update
