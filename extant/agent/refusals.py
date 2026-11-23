"""Refusal messages: a product surface, one shape per reason.

Four rules hold for every message. It names the specific thing that could not be
established, with the entity and the date that were used. It says what was found instead,
when something was. It suggests rephrasing only where that would change the outcome
(``out_of_scope`` and ``entity_required``). And it carries no apology longer than its
information. Where something was found but not stood behind, the message ends by sending
the reader to the instrument itself, which is the most useful sentence in it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from extant.agent.models import AgentState, Candidate, Citation, RefusalReason

CHECK_THE_INSTRUMENT = "Check the instrument itself before relying on this."


def _who(state: AgentState) -> str:
    return f"an entity of type {state.entity_type}" if state.entity_type else "any entity type"


def _when(state: AgentState) -> str:
    on = state.as_of or state.today
    return on.isoformat()


def nearest_citation(c: Candidate) -> Citation:
    """A paragraph that was found but not stood behind, cited whole."""
    return Citation(
        para_id=c.para_id,
        doc_id=c.doc_id,
        doc_title=c.doc_title,
        paragraph_number=c.paragraph_number or c.para_id.split("#", 1)[1],
        effective_from=c.valid_from,
        effective_to=c.valid_to,
        entity_scope=list(c.entity_scope),
        authority_rank=c.authority_rank,
        binding=c.binding,
        source_url=c.source_url,  # type: ignore[arg-type]
        source_page=c.source_page,
        quote_start=0,
        quote_end=len(c.body),
    )


def _withdrawn_after(state: AgentState) -> list[str]:
    """Circulars the question names that RBI withdrew after the date asked about."""
    on = state.as_of or state.today
    out = []
    for ref in state.references:
        for w in ref.withdrawals:
            if w.effective_on > on:
                out.append(
                    f"{ref.surface} was withdrawn by {w.withdrawn_by_doc_id} with effect from"
                    f" {w.effective_on.isoformat()}, which is after the date you asked about,"
                    " so it may still have applied"
                )
    return out


def _superseded(state: AgentState, dropped: Sequence[Candidate]) -> list[str]:
    on = state.as_of or state.today
    out = []
    for ref in state.references:
        for w in ref.withdrawals:
            if w.effective_on <= on:
                out.append(
                    f"circular {ref.surface} was withdrawn by {w.withdrawn_by_doc_id} with effect"
                    f" from {w.effective_on.isoformat()}"
                )
    for c in dropped:
        if c.valid_to is not None and c.valid_to <= c.asked_for:
            out.append(
                f"paragraph {c.paragraph_number or c.para_id} of {c.doc_title} ceased to be in"
                f" force on {c.valid_to.isoformat()}"
            )
        elif c.valid_from > c.asked_for:
            out.append(
                f"paragraph {c.paragraph_number or c.para_id} of {c.doc_title} comes into force"
                f" on {c.valid_from.isoformat()}"
            )
    return out


def message(
    state: AgentState,
    reason: RefusalReason,
    *,
    probe: Sequence[Candidate] = (),
    groups: dict[tuple[str, ...], list[Candidate]] | None = None,
) -> str:
    who, when = _who(state), _when(state)
    detail = state.refusal_detail
    if reason is RefusalReason.OUT_OF_SCOPE:
        about = f" ({detail})" if detail else ""
        return (
            f"This question{about} is outside what Extant covers: RBI directions and the rules"
            " in them for regulated entities. Ask about an RBI requirement and it can be"
            " answered with a citation."
        )
    if reason is RefusalReason.COMPUTATION_REQUESTED:
        return (
            "Extant states what RBI's directions require and cites them; it does not calculate"
            " figures for an institution"
            + (f" (the question asks to {detail})." if detail else ".")
            + " Asking what a rule requires, or whether something is permitted, is answered."
        )
    if reason is RefusalReason.ENTITY_REQUIRED:
        parts = []
        for scope, cands in sorted((groups or {}).items()):
            paras = ", ".join(
                f"paragraph {c.paragraph_number or c.para_id.partition('#')[2]} of {c.doc_title}"
                for c in cands[:2]
            )
            parts.append(f"for {', '.join(scope)}: {paras}")
        return (
            f"The provision that governs this differs by entity type, and none was given, so"
            f" nothing is assumed. In force on {when}, " + "; ".join(parts) + ". Ask again with"
            " the entity type to get the provision that applies."
        )
    if reason is RefusalReason.NO_SUPPORT_FOUND:
        base = f"No RBI direction paragraph in force for {who} on {when} establishes this"
        # The composer's own sentence ends with a full stop; one is enough.
        base += f": {detail.rstrip('. ')}." if detail else "."
        later = _withdrawn_after(state)
        if later:
            # Upper case the first letter only: str.capitalize would lower case the rest, and
            # mangle a circular reference ("DBOD.No." into "Dbod.no.").
            joined = "; ".join(later)
            return f"{base} {joined[:1].upper()}{joined[1:]}. {CHECK_THE_INSTRUMENT}"
        return base
    if reason is RefusalReason.SUPERSEDED_ONLY:
        found = _superseded(state, probe)
        listed = "; ".join(found) if found else "it was repealed"
        return (
            f"What addresses this was not in force for {who} on {when}: {listed}. Nothing in"
            f" force on that date replaces it in what was found. {CHECK_THE_INSTRUMENT}"
        )
    if reason is RefusalReason.NON_BINDING_SUPPORT_ONLY:
        return (
            f"Only non-binding material (an FAQ or a press release) addresses this for {who} on"
            f" {when}. It is listed below, but it does not have the force of a direction."
            f" {CHECK_THE_INSTRUMENT}"
        )
    if reason is RefusalReason.VERIFICATION_FAILED:
        what = detail or "a citation"
        return (
            f"An answer was drafted for {who} on {when}, but {what} did not hold against the"
            f" cited text, so it is withheld. The paragraphs it would have relied on are listed."
            f" {CHECK_THE_INSTRUMENT}"
        )
    raise ValueError(f"the graph does not raise {reason}")


def group_by_scope(candidates: Sequence[Candidate]) -> dict[tuple[str, ...], list[Candidate]]:
    groups: dict[tuple[str, ...], list[Candidate]] = defaultdict(list)
    for c in candidates:
        groups[c.entity_scope].append(c)
    return dict(groups)


def latest(on: date | None, today: date) -> date:
    return on or today
