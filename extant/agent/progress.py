"""Progress events: what the graph has actually done so far, for a client that shows the work.

Every event comes from a real transition. ``run_node`` announces a node as it starts and
again when it has finished, with what that node wrote: how many candidates remain, the rows
the gate dropped, the checks recorded so far. No event is produced on a timer, so a node that
takes four seconds is four seconds between its two events.

The events are a projection of the state, shaped here so the transport above shows them as
given and computes nothing: every count in an event is counted here. The answer is not among
them. It arrives once, verified, as the final ``AskResponse``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from extant.agent.models import Dropped, NodeCost


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckView(_Event):
    name: str  # the key in the state's checks, such as claim0.quote_match
    passed: bool
    detail: str


class NodeStarted(_Event):
    event: Literal["node_started"] = "node_started"
    node: str


class NodeFinished(_Event):
    """One node done. A field is None (or empty) when this node did not write it."""

    event: Literal["node_finished"] = "node_finished"
    node: str
    latency_ms: float
    model_calls: int
    path: str | None = None  # parse: the route it chose
    entity_type: str | None = None  # parse
    as_of: date | None = None  # parse
    as_of_source: str | None = None  # parse: submitted, question or today
    candidates: int | None = None  # paragraphs held after this node
    neighbours: int | None = None  # retrieve: adjacent paragraphs fetched for expand
    dropped: list[Dropped] = []  # rows this node added, in the order it added them
    drafted: int | None = None  # compose: claims drafted
    verified: int | None = None  # verify: claims that passed every check
    checks: list[CheckView] = []  # every check recorded so far, when this node wrote any
    degraded: list[str] = []  # markers this node added
    refusal_reason: str | None = None  # when this node decided to refuse


class Restarted(_Event):
    """The service is running the graph again from the start (the database failed once).
    Everything shown so far belongs to the abandoned attempt."""

    event: Literal["restarted"] = "restarted"
    attempt: int
    reason: str


Progress = NodeStarted | NodeFinished | Restarted
Observe = Callable[[Progress], None]


def finished(name: str, update: Mapping[str, Any], cost: NodeCost) -> NodeFinished:
    """What node ``name`` did, from the update it returned and its cost row."""
    fields: dict[str, Any] = {}
    if name == "parse":
        for key in ("path", "entity_type", "as_of", "as_of_source"):
            if key in update:
                fields[key] = update[key]
    if "candidates" in update:
        fields["candidates"] = len(update["candidates"])
    if "neighbours" in update and name == "retrieve":
        fields["neighbours"] = len(update["neighbours"])
    if "draft_claims" in update:
        fields["drafted"] = len(update["draft_claims"])
    if "verified_claims" in update:
        fields["verified"] = len(update["verified_claims"])
    if "checks" in update:
        fields["checks"] = [
            CheckView(name=key, passed=r.passed, detail=r.detail)
            for key, r in update["checks"].items()
        ]
    reason = update.get("refusal_reason")
    return NodeFinished(
        node=name,
        latency_ms=cost.latency_ms,
        model_calls=cost.calls,
        dropped=list(update.get("dropped", [])),
        degraded=list(update.get("degraded", [])),
        refusal_reason=None if reason is None else str(reason),
        **fields,
    )
