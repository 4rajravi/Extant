"""Replaying a recorded fixture through the real service, with no network and no cost.

A fixture holds every index call and model call one question made when it was recorded.
``serve`` runs the same question through ``Service`` (admission, the cap, the graph, the
ladder) with those calls served back, so a replay exercises everything a live request does
except the network. ``faults`` turns a dependency off, to reach a degradation rung.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from extant.agent.examples import ExampleCache
from extant.agent.index import RecordedIndex
from extant.agent.llm import Completion, ReplayModel, StructuredModel
from extant.agent.models import AgentState
from extant.agent.runtime import Index
from extant.agent.service import Served, Service
from extant.guards.errors import ModelUnavailableError
from extant.guards.middleware import MemoryUsageStore, UsageStore

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agent"
ROLES = {"parse": "claude-haiku-5-5", "compose": "claude-opus-5-5", "judge": "claude-haiku-5-5"}


def load(name_or_path: str | Path) -> dict[str, Any]:
    path = Path(name_or_path)
    if not path.suffix:
        path = FIXTURES / f"{name_or_path}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@dataclass
class Down:
    """A model that is down: every call fails as the middleware reports it."""

    name: str

    @property
    def model(self) -> str:
        return self.name

    def complete(self, schema: Any, *, system: str, user: str) -> Completion:
        raise ModelUnavailableError(f"{self.name} unavailable after 2 attempts: overloaded")


def serve(
    data: Mapping[str, Any],
    *,
    store: UsageStore | None = None,
    down: tuple[str, ...] = (),
    open_index: Callable[[], Index] | None = None,
    cache: ExampleCache | None = None,
    log: Callable[[Mapping[str, Any]], None] | None = None,
) -> Served:
    models: dict[str, StructuredModel] = {
        role: Down(model) if role in down else ReplayModel(model, data["models"], source=FIXTURES)
        for role, model in ROLES.items()
    }
    index = RecordedIndex(data["index"], data["embedding_model"])
    service = Service(
        index_version=data["index_version"],
        open_index=open_index or (lambda: index),
        models=models,
        store=store or MemoryUsageStore(cap=1_000_000),
        entities=data["entities"],
        cache=cache,
        log=log or (lambda record: None),
        day=lambda: date.fromisoformat(data["today"]),
    )
    spec = data["spec"]
    return service.run(
        spec["question"],
        entity=spec["entity"],
        as_of=date.fromisoformat(spec["as_of"]) if spec["as_of"] else None,
        request_id=spec["name"],
    )


def replay(data: Mapping[str, Any]) -> AgentState:
    served = serve(data)
    if served.state is None:
        raise AssertionError(f"the graph did not run: {served.response.degraded}")
    return served.state.model_copy(update={"response": served.response})
