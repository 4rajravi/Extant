"""parse: what the question asks, about whom, on what date, and which route it takes.

Deterministic first. A pattern that puts the question out of scope, or asks for a
computation, settles the route with no model call; so does a literal instrument reference,
which takes the fast path. Only a question none of those settle reaches the cheap model,
which classifies scope and kind and extracts the entity and the dates. Precedence is out of
scope, then computation, then a reference, so an out of scope question carrying a reference
exits rather than taking the fast path.

``entity_type`` and ``as_of`` are written here and nowhere else, exactly once. What the
caller supplied wins over what the question says; with neither, ``as_of`` is today and the
``as_of_assumed`` caveat is attached rather than the date passing silently.
"""

from __future__ import annotations

from typing import Any

from extant.agent import patterns, prompts
from extant.agent.models import AgentState, Caveat, ParseOutput
from extant.agent.runtime import Given, Spend, parsed
from extant.core.identity import find_references
from extant.guards.errors import ModelUnavailableError


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    q = state.raw_query
    refs = find_references(q)
    caveats: list[Caveat] = []
    if patterns.advice_phrasing(q):
        caveats.append(Caveat.NOT_LEGAL_ADVICE)
    update: dict[str, Any] = {
        "literal_refs": [c for _, c in refs],
        "surface_refs": [s for s, _ in refs],
    }
    entity, as_of = state.submitted_entity, state.submitted_as_of
    source = "submitted" if as_of is not None else None
    oos, comp = patterns.out_of_scope(q), patterns.computation_requested(q)
    if oos:
        update |= {"path": "out_of_scope", "refusal_detail": f"it concerns {oos}"}
    elif comp:
        update |= {"path": "refuse", "refusal_detail": comp.lower()}
    elif refs:
        update |= {"path": "fast", "query_kind": "traceback", "topic_terms": [s for s, _ in refs]}
    else:
        if given.model is None:
            raise RuntimeError("parse needs a model for a question no pattern settles")
        try:
            done = spend.complete(
                given.model, ParseOutput, system=prompts.parse_system(given.entities), user=q
            )
        except ModelUnavailableError:
            # Rung 3: no classification, so no composed answer either. The deterministic
            # patterns above have already run; what is left is retrieved and shown as is.
            update |= {
                "path": "full",
                "retrieval_only": True,
                "degraded": ["retrieval_only"],
            }
            caveats.append(Caveat.DEGRADED_RETRIEVAL)
            if as_of is None:
                as_of, source = state.today, "today"
                caveats.append(Caveat.AS_OF_ASSUMED)
            return update | {
                "entity_type": entity,
                "as_of": as_of,
                "as_of_source": source,
                "caveats": caveats,
            }
        out = parsed(done, ParseOutput)
        update |= {"query_kind": out.query_kind, "topic_terms": out.topic_terms[:6]}
        if entity is None and out.entity_type in given.entities:
            entity, source = out.entity_type, "question"
        if as_of is None and out.as_of is not None:
            as_of, source = out.as_of, "question"
        if not out.in_scope:
            update |= {"path": "out_of_scope", "refusal_detail": None}
        elif out.computation_requested:
            update |= {"path": "refuse", "refusal_detail": None}
        elif out.query_kind == "diff" and out.compare_as_of is not None and as_of is not None:
            update |= {"path": "diff", "compare_as_of": out.compare_as_of}
        else:
            update["path"] = "full"
    if as_of is None:
        as_of, source = state.today, "today"
        caveats.append(Caveat.AS_OF_ASSUMED)
    update |= {"entity_type": entity, "as_of": as_of, "as_of_source": source, "caveats": caveats}
    return update
