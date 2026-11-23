"""retrieve: the hybrid retrieval of Phase 2, one of the two nodes that read the database.

With an entity type, retrieval is filtered to it. Without one it runs across every entity
type, and ``authority`` then compares the scopes of what came back. Two queries run with one
embedding of the question:

- the ranked search, in force on the as-of date, with each result's ordinal neighbours,
  which ``expand`` admits as unscored context;
- a supersession probe over every version, whose results that are not in force are added so
  the gate can remove them on the record. That is what fills ``dropped``, and what makes
  ``superseded_only`` reachable when the only plausible answer is out of force.

On the diff path the top paragraph is looked up again in the version in force on the second
date. If the embedding call fails, retrieval falls back to the lexical arm alone and says so
with the ``degraded_retrieval`` caveat.
"""

from __future__ import annotations

from typing import Any

from extant.agent.models import AgentState, Caveat
from extant.agent.runtime import Given, InvariantError, Spend, as_of

TOP_N = 6  # ranked candidates passed on; their neighbours come with them
PROBE_N = 5


def run(state: AgentState, given: Given, spend: Spend) -> dict[str, Any]:
    index = given.index
    if index is None:
        raise RuntimeError("retrieve needs the index")
    on = as_of(state)
    codes = (state.entity_type,) if state.entity_type else index.entity_codes()
    query = state.raw_query
    found = index.search(query, codes, on, validity="in_force", top_n=TOP_N)
    probe = index.search(query, codes, on, validity="any", top_n=PROBE_N)
    spend.embedding_model = index.embedding_model
    spend.embedding_calls += found.embedding_calls + probe.embedding_calls
    spend.embedding_tokens += found.embedding_tokens + probe.embedding_tokens
    have = {c.para_id for c in found.candidates}
    stale = [c for c in probe.candidates if c.para_id not in have and not c.in_force()]
    candidates = [*found.candidates, *stale]
    update: dict[str, Any] = {"candidates": candidates, "neighbours": found.neighbours}
    degraded = list(dict.fromkeys([*found.degraded, *probe.degraded]))
    if degraded:
        update |= {"degraded": degraded, "caveats": [Caveat.DEGRADED_RETRIEVAL]}
    if state.path == "diff" and found.candidates:
        if state.compare_as_of is None:
            raise InvariantError("the diff path needs a second date")
        top = found.candidates[0]
        other = index.version_at(top.para_id, state.compare_as_of)
        pair = [top.model_copy(update={"origin": "diff"})]
        if other is not None and other.para_id != top.para_id:
            pair.append(other)
        update |= {"candidates": pair, "neighbours": []}
    return update
