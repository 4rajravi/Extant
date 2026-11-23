"""How a node runs: what it may write, what it may touch, and what it cost.

Every node is wrapped by ``run_node``. The wrapper:

- refuses an update to a field the node does not own (``WRITES``, the invariant table);
- refuses a second write of ``entity_type`` or ``as_of``;
- refuses a removal from ``candidates`` with no matching row in ``dropped``, and after the
  gate has run refuses any candidate that was not there before (they may only shrink);
- appends the node's ``NodeCost``, including a zero row for a node that spent nothing;
- validates the merged state, so a bad state fails on the hop that made it.

Each node is handed only what it needs. ``retrieve`` and ``resolve_reference`` receive the
index; ``parse``, ``compose`` and ``judge`` receive their model; the rest receive neither, so
a deterministic node cannot reach a database or a model even by mistake.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from extant.agent.arms import BASELINE, Arm
from extant.agent.llm import Completion, StructuredModel
from extant.agent.models import AgentState, Candidate, NodeCost, ReferenceRecord
from extant.agent.pricing import chat_eur, embedding_eur

NODES = (
    "parse",
    "refuse_early",
    "resolve_reference",
    "retrieve",
    "expand",
    "gate",
    "authority",
    "compose",
    "verify",
    "judge",
    "finalize",
)

# The invariant table. A node writing any other field is a bug.
WRITES: dict[str, frozenset[str]] = {
    "parse": frozenset(
        {
            "query_kind",
            "entity_type",
            "as_of",
            "as_of_source",
            "compare_as_of",
            "literal_refs",
            "surface_refs",
            "topic_terms",
            "path",
            "caveats",
            "refusal_detail",
            "degraded",
            "retrieval_only",
        }
    ),
    "refuse_early": frozenset({"outcome", "refusal_reason", "refusal_detail"}),
    "resolve_reference": frozenset({"candidates", "references"}),
    "retrieve": frozenset({"candidates", "neighbours", "degraded", "caveats"}),
    "expand": frozenset({"candidates", "neighbours"}),
    "gate": frozenset(
        {
            "candidates",
            "dropped",
            "set_aside",
            "gate_ran",
            "refusal_reason",
            "refusal_detail",
        }
    ),
    "authority": frozenset(
        {
            "candidates",
            "dropped",
            "set_aside",
            "caveats",
            "refusal_reason",
            "refusal_detail",
        }
    ),
    "compose": frozenset(
        {
            "draft_claims",
            "missing",
            "checks",
            "refusal_reason",
            "refusal_detail",
            "degraded",
            "caveats",
            "retrieval_only",
        }
    ),
    "verify": frozenset(
        {"verified_claims", "checks", "residual", "refusal_reason", "refusal_detail"}
    ),
    "judge": frozenset(
        {"checks", "refusal_reason", "refusal_detail", "degraded", "caveats", "retrieval_only"}
    ),
    "finalize": frozenset({"outcome", "response", "caveats"}),
    # Eval only (extant.agent.arms): the bounded retry after a recoverable verify failure.
    "retry": frozenset(
        {
            "draft_claims",
            "verified_claims",
            "residual",
            "checks",
            "refusal_reason",
            "refusal_detail",
        }
    ),
}
APPEND_ONLY = frozenset({"dropped", "set_aside", "caveats", "degraded", "node_log"})
SET_ONCE = frozenset({"entity_type", "as_of"})


class InvariantError(AssertionError):
    """A node broke the invariant table."""


# --------------------------------------------------------------------------- what nodes touch


@dataclass(frozen=True)
class Search:
    candidates: list[Candidate]
    neighbours: list[Candidate]
    embedding_calls: int = 0
    embedding_tokens: int = 0
    degraded: list[str] = field(default_factory=list)


class Index(Protocol):
    """The database, as the two nodes allowed to read it see it."""

    @property
    def embedding_model(self) -> str: ...

    def entity_codes(self) -> tuple[str, ...]: ...

    def search(
        self,
        query: str,
        entity_codes: tuple[str, ...],
        as_of: date,
        *,
        validity: Literal["in_force", "any"],
        top_n: int,
    ) -> Search: ...

    def reference(
        self, query: str, entity_codes: tuple[str, ...], as_of: date
    ) -> tuple[list[Candidate], list[ReferenceRecord]]: ...

    def version_at(self, para_id: str, day: date) -> Candidate | None: ...


@dataclass
class Spend:
    """What a node spent; turned into its NodeCost row."""

    calls: int = 0
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_calls: int = 0
    embedding_tokens: int = 0
    embedding_model: str | None = None
    # Time spent inside model calls: as measured here, and as the model reported it. They
    # differ only on a replay, where the call returns at once with the recorded latency.
    model_wall_ms: float = 0.0
    model_reported_ms: float = 0.0

    def complete(
        self, model: StructuredModel, schema: type[BaseModel], *, system: str, user: str
    ) -> Completion:
        start = time.perf_counter()
        done = model.complete(schema, system=system, user=user)
        self.model_wall_ms += (time.perf_counter() - start) * 1000
        self.model_reported_ms += done.latency_ms
        self.calls += 1
        self.model = done.model
        self.input_tokens += done.input_tokens
        self.output_tokens += done.output_tokens
        return done

    def eur(self, eur_per_usd: float) -> float:
        total = 0.0
        if self.calls and self.model:
            total += chat_eur(self.model, self.input_tokens, self.output_tokens, eur_per_usd)
        if self.embedding_tokens and self.embedding_model:
            total += embedding_eur(self.embedding_model, self.embedding_tokens, eur_per_usd)
        return total


@dataclass(frozen=True)
class Deps:
    """Everything outside the state a node may use. Built once per graph."""

    models: Mapping[str, StructuredModel] = field(default_factory=dict)
    index: Index | None = None
    eur_per_usd: float = 0.90
    # Entity codes and labels, read once when the graph is built: parse shows them to the
    # model, which may name only one of them.
    entities: Mapping[str, str] = field(default_factory=dict)
    arm: Arm = BASELINE  # serving never sets this; only the Phase 6 harness does


@dataclass(frozen=True)
class Given:
    """The slice of ``Deps`` one node is handed."""

    model: StructuredModel | None = None
    index: Index | None = None
    entities: Mapping[str, str] = field(default_factory=dict)
    arm: Arm = BASELINE


NodeFn = Callable[[AgentState, Given, Spend], dict[str, Any]]
_MODEL_NODES = frozenset({"parse", "compose", "judge"})
_INDEX_NODES = frozenset({"retrieve", "resolve_reference"})


def as_of(state: AgentState) -> date:
    """The resolved as-of date; parse always sets it, so its absence is a graph bug."""
    if state.as_of is None:
        raise InvariantError("as_of is unset after parse")
    return state.as_of


def parsed[S: BaseModel](done: Completion, schema: type[S]) -> S:
    if not isinstance(done.parsed, schema):
        raise InvariantError(f"expected {schema.__name__}, got {type(done.parsed).__name__}")
    return done.parsed


def given(name: str, deps: Deps) -> Given:
    if name == "retry":  # eval only: the retry is a second composer call
        return Given(model=deps.models.get("compose"), arm=deps.arm)
    return Given(
        model=deps.models.get(name) if name in _MODEL_NODES else None,
        index=deps.index if name in _INDEX_NODES else None,
        entities=deps.entities if name == "parse" else {},
        arm=deps.arm,
    )


# --------------------------------------------------------------------------- the wrapper


def _merge(state: AgentState, update: Mapping[str, Any]) -> dict[str, Any]:
    merged = state.model_dump()
    for key, value in update.items():
        merged[key] = (merged[key] + list(value)) if key in APPEND_ONLY else value
    return merged


def _check(name: str, state: AgentState, update: Mapping[str, Any]) -> None:
    stray = set(update) - WRITES[name]
    if stray:
        raise InvariantError(f"{name} wrote fields it does not own: {sorted(stray)}")
    for key in SET_ONCE & set(update):
        if getattr(state, key) is not None:
            raise InvariantError(f"{name} wrote {key} a second time")
    for key in APPEND_ONLY & set(update):
        if not isinstance(update[key], list):
            raise InvariantError(f"{name}: {key} is append only; pass the new rows as a list")
    if "candidates" in update:
        before = {c.para_id for c in state.candidates}
        after = {c.para_id for c in update["candidates"]}
        removed = before - after
        recorded = {d.para_id for d in [*state.dropped, *update.get("dropped", [])]}
        if removed - recorded:
            raise InvariantError(f"{name} removed {sorted(removed - recorded)} with no dropped row")
        if state.gate_ran and after - before:
            raise InvariantError(
                f"{name} added candidates after the gate: {sorted(after - before)}"
            )


def run_node(name: str, fn: NodeFn, deps: Deps) -> Callable[[AgentState], dict[str, Any]]:
    handed = given(name, deps)

    def node(state: AgentState) -> dict[str, Any]:
        spend = Spend()
        start = time.perf_counter()
        update = fn(state, handed, spend)
        wall = (time.perf_counter() - start) * 1000
        latency = max(wall - spend.model_wall_ms, 0.0) + spend.model_reported_ms
        _check(name, state, update)
        cost = NodeCost(
            node=name,
            calls=spend.calls,
            model=spend.model,
            input_tokens=spend.input_tokens,
            output_tokens=spend.output_tokens,
            embedding_calls=spend.embedding_calls,
            embedding_tokens=spend.embedding_tokens,
            eur=spend.eur(deps.eur_per_usd),
            latency_ms=round(latency, 2),
        )
        full = {**update, "node_log": [cost]}
        AgentState.model_validate(_merge(state, full))  # a bad state fails on this hop
        return full

    node.__name__ = name
    return node
