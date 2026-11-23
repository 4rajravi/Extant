"""A runtime for the HTTP API with no database and no network.

``ask`` runs a recorded fixture through the real ``Service`` (admission, the cap, the graph,
the ladder) with the fixture's index and model calls served back, and keeps what the graph
was given: every prompt each model saw and every query the index was asked. That is how a
test proves the graph never received an identifier.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from extant import catalog
from extant.agent.examples import ExampleCache
from extant.agent.index import RecordedIndex
from extant.agent.llm import Completion, ReplayModel, StructuredModel
from extant.agent.models import AskResponse, Candidate, ReferenceRecord
from extant.agent.runtime import Search
from extant.agent.service import Readiness, Service
from extant.guards.middleware import MemoryUsageStore
from tests import replay_support
from tests.mcp_support import PARA, VERSION


@dataclass
class Seen:
    prompts: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


@dataclass
class _Watched:
    inner: StructuredModel
    seen: Seen

    @property
    def model(self) -> str:
        return self.inner.model

    def complete(self, schema: Any, *, system: str, user: str) -> Completion:
        self.seen.prompts.append(user)
        return self.inner.complete(schema, system=system, user=user)


@dataclass
class _WatchedIndex:
    inner: RecordedIndex
    seen: Seen

    @property
    def embedding_model(self) -> str:
        return self.inner.embedding_model

    def entity_codes(self) -> tuple[str, ...]:
        return self.inner.entity_codes()

    def search(self, query: str, entity_codes: tuple[str, ...], as_of: date, **kw: Any) -> Search:
        self.seen.queries.append(query)
        return self.inner.search(query, entity_codes, as_of, **kw)

    def reference(
        self, query: str, entity_codes: tuple[str, ...], as_of: date
    ) -> tuple[list[Candidate], list[ReferenceRecord]]:
        self.seen.queries.append(query)
        return self.inner.reference(query, entity_codes, as_of)

    def version_at(self, para_id: str, day: date) -> Candidate | None:
        return self.inner.version_at(para_id, day)


@dataclass
class FakeRuntime:
    fixture: str = "full_answer_third_person"
    store: MemoryUsageStore = field(default_factory=lambda: MemoryUsageStore(cap=1_000_000))
    examples: ExampleCache | None = field(default_factory=ExampleCache.load)
    seen: Seen = field(default_factory=Seen)
    ready: Readiness = field(default_factory=lambda: Readiness([], []))
    answered_today: int = 0
    asked: list[tuple[str, str | None, date | None]] = field(default_factory=list)
    closed: bool = False

    @property
    def index_version(self) -> str:
        return VERSION

    @property
    def cache(self) -> ExampleCache | None:
        return self.examples

    @property
    def cap(self) -> int:
        return self.store.cap

    def ask(self, question: str, entity: str | None, as_of: date | None) -> AskResponse:
        self.asked.append((question, entity, as_of))
        data = replay_support.load(self.fixture)
        models = {
            role: _Watched(
                ReplayModel(name, data["models"], source=replay_support.FIXTURES), self.seen
            )
            for role, name in replay_support.ROLES.items()
        }
        index = _WatchedIndex(RecordedIndex(data["index"], data["embedding_model"]), self.seen)
        service = Service(
            index_version=data["index_version"],
            open_index=lambda: index,
            models=models,
            store=self.store,
            entities=data["entities"],
            cache=self.examples,
            log=lambda record: None,
            day=lambda: date.fromisoformat(data["today"]),
        )
        return service.ask(question, entity=entity, as_of=as_of)

    def paragraph(self, para_id: str) -> catalog.ParagraphRecord | None:
        return PARA if para_id == PARA.para_id else None

    def readiness(self) -> Readiness:
        return self.ready

    def answered(self, day: date) -> int:
        return self.answered_today

    def close(self) -> None:
        self.closed = True


def spec(fixture: str) -> dict[str, Any]:
    """The request body that replays ``fixture``: its own question, entity and date."""
    s = replay_support.load(fixture)["spec"]
    return {"question": s["question"], "entity": s["entity"], "as_of": s["as_of"]}


def today_of(fixture: str) -> date:
    return date.fromisoformat(replay_support.load(fixture)["today"])


def all_text(items: Sequence[str]) -> str:
    return "\n".join(items)
