"""The layers below, as the MCP server sees them.

``Backend`` is the whole of what the server may call. Each method is one existing function:
the Service for ``ask``, the agent's index for ``search``, ``extant.catalog`` for the rest.
``PgBackend`` opens a read only connection per call and closes it, through
``extant.agent.serving``. Tests give the server a fake.

There is no method here that searches without an entity and a date, and none that reads
untrusted rows or paragraphs out of force: the server cannot offer what this does not have.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from extant import catalog
from extant.agent.index import PgIndex
from extant.agent.models import AskResponse
from extant.agent.runtime import Search
from extant.agent.serving import Serving, connection, service
from extant.guards import logs, pii


@dataclass(frozen=True)
class Searched:
    query: str  # redacted
    redactions: tuple[str, ...]
    search: Search


class Backend(Protocol):
    @property
    def index_version(self) -> str: ...

    @property
    def entities(self) -> Mapping[str, str]: ...  # code -> label

    def ask(self, question: str, entity: str | None, as_of: date | None) -> AskResponse: ...

    def search(self, query: str, entity: str, as_of: date, top_n: int) -> Searched: ...

    def paragraph(self, para_id: str) -> catalog.ParagraphRecord | None: ...

    def resolve(self, reference: str, entity: str, as_of: date) -> catalog.Resolution: ...

    def diff(self, doc_id: str, from_date: date, to_date: date) -> catalog.VersionDiff | None: ...

    def applicable(
        self, entity: str, as_of: date, topic: str | None
    ) -> list[catalog.DirectionRecord]: ...

    def manifest(self) -> dict[str, Any]: ...

    def entity_types(self) -> list[catalog.EntityTypeRecord]: ...


class PgBackend:
    def __init__(
        self, serving: Serving, *, log: Callable[[Mapping[str, Any]], None] = logs.emit
    ) -> None:
        self.serving = serving
        self.log = log  # over stdio, stdout is the protocol channel: pass logs.emit_stderr

    @property
    def index_version(self) -> str:
        return self.serving.schema

    @property
    def entities(self) -> Mapping[str, str]:
        return self.serving.entities

    def ask(self, question: str, entity: str | None, as_of: date | None) -> AskResponse:
        with service(self.serving, log=self.log) as s:
            return s.ask(question, entity=entity, as_of=as_of)

    def search(self, query: str, entity: str, as_of: date, top_n: int) -> Searched:
        # The query goes to the embedding provider, so it is redacted first, as the Service
        # redacts a question at admission.
        clean = pii.redact(query)
        with connection(self.serving) as conn:
            index = PgIndex(conn, self.serving.schema, self.serving.embedder)
            found = index.search(clean.text, (entity,), as_of, validity="in_force", top_n=top_n)
        return Searched(clean.text, clean.kinds, found)

    def paragraph(self, para_id: str) -> catalog.ParagraphRecord | None:
        with connection(self.serving) as conn:
            return catalog.fetch_paragraph(conn, para_id)

    def resolve(self, reference: str, entity: str, as_of: date) -> catalog.Resolution:
        with connection(self.serving) as conn:
            return catalog.resolve_reference(conn, reference, as_of, (entity,))

    def diff(self, doc_id: str, from_date: date, to_date: date) -> catalog.VersionDiff | None:
        with connection(self.serving) as conn:
            return catalog.diff_versions(conn, doc_id, from_date, to_date)

    def applicable(
        self, entity: str, as_of: date, topic: str | None
    ) -> list[catalog.DirectionRecord]:
        with connection(self.serving) as conn:
            return catalog.directions_in_force(conn, (entity,), as_of, topic)

    def manifest(self) -> dict[str, Any]:
        with connection(self.serving) as conn:
            return catalog.manifest(conn, self.serving.schema)

    def entity_types(self) -> list[catalog.EntityTypeRecord]:
        with connection(self.serving) as conn:
            return catalog.entity_taxonomy(conn)
