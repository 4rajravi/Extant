"""The agent graph: eleven nodes, one routing edge, no cycle.

    parse -(route)-> refuse_early ------------------------------------> finalize
                \\-> resolve_reference ------------\
                 \\-> retrieve -> expand ----------> gate -> authority -> compose
                                                    -> verify -> judge -> finalize

Routing is a conditional edge after ``parse``, on the path ``parse`` decided in precedence
order: out of scope, then a computation or advice request, then a literal reference (the
fast path), then a diff question, then everything else. After ``gate``, ``authority``,
``compose`` and ``verify``, a refusal reason goes straight to ``finalize``.

The graph is stateless per request: no checkpointer, no memory, no tool loop. There is no
retry into ``compose`` after a failed verification; whether one earns its doubled worst case
cost is a Phase 6 ablation, so it is not here, not even behind a flag.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from langgraph.graph import END, START, StateGraph

from extant.agent.models import AgentState, AskResponse
from extant.agent.nodes import (
    authority,
    compose,
    expand,
    finalize,
    gate,
    judge,
    parse,
    refuse_early,
    resolve_reference,
    retrieve,
    verify,
)
from extant.agent.runtime import Deps, run_node

_FNS = {
    "parse": parse.run,
    "refuse_early": refuse_early.run,
    "resolve_reference": resolve_reference.run,
    "retrieve": retrieve.run,
    "expand": expand.run,
    "gate": gate.run,
    "authority": authority.run,
    "compose": compose.run,
    "verify": verify.run,
    "judge": judge.run,
    "finalize": finalize.run,
}
ROUTES = {
    "out_of_scope": "refuse_early",
    "refuse": "refuse_early",
    "fast": "resolve_reference",
    "diff": "retrieve",
    "full": "retrieve",
}


def route(state: AgentState) -> str:
    """The conditional edge after parse."""
    if state.path is None:
        raise RuntimeError("parse decided no path")
    return ROUTES[state.path]


def _onward(to: str) -> Any:
    def edge(state: AgentState) -> str:
        if state.refusal_reason is not None or (state.retrieval_only and to != "authority"):
            return "finalize"
        return to

    return edge


def build(deps: Deps) -> Any:
    g = StateGraph(AgentState)
    for name, fn in _FNS.items():
        g.add_node(name, run_node(name, fn, deps))  # type: ignore[call-overload]
    g.add_edge(START, "parse")
    g.add_conditional_edges("parse", route, sorted(set(ROUTES.values())))
    g.add_edge("refuse_early", "finalize")
    g.add_edge("resolve_reference", "gate")
    g.add_edge("retrieve", "expand")
    g.add_edge("expand", "gate")
    g.add_conditional_edges("gate", _onward("authority"), ["authority", "finalize"])
    g.add_conditional_edges("authority", _onward("compose"), ["compose", "finalize"])
    g.add_conditional_edges("compose", _onward("verify"), ["verify", "finalize"])
    g.add_conditional_edges("verify", _onward("judge"), ["judge", "finalize"])
    g.add_edge("judge", "finalize")  # judge ends either way: answer, refusal or retrieval only
    g.add_edge("finalize", END)
    return g.compile()


def ask(
    graph: Any,
    question: str,
    *,
    index_version: str,
    entity: str | None = None,
    as_of: date | None = None,
    today: date | None = None,
    request_id: str | None = None,
) -> AgentState:
    """Run one question through the graph and return the final state."""
    start = AgentState(
        request_id=request_id or uuid.uuid4().hex,
        index_version=index_version,
        raw_query=question,
        submitted_entity=entity,
        submitted_as_of=as_of,
        today=today or date.today(),
    )
    out = graph.invoke(start)
    return out if isinstance(out, AgentState) else AgentState.model_validate(out)


def response(state: AgentState) -> AskResponse:
    if state.response is None:
        raise RuntimeError("the graph ended without a response")
    return state.response
