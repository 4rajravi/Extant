"""The layers below, as the HTTP API sees them.

``Runtime`` is the whole of what the API may call: the Service for an answer (with an
observer for the streaming variant), the catalog for a citation (and its alias hit counter)
and for the entity taxonomy, the readiness check and the cap's counter. The API
owns transport and the operational guards; nothing here is retrieval or agent logic.
``PgRuntime`` runs on ``extant.agent.serving`` and its pool. Tests give the API a fake.

``cache`` is held in memory, loaded once from the file baked into the package: the examples
are served with no database and no model, which is the point of them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, Protocol

from extant import catalog
from extant.agent.examples import ExampleCache
from extant.agent.models import AskResponse
from extant.agent.progress import Observe
from extant.agent.service import Readiness
from extant.agent.serving import Serving, connection, service
from extant.guards import logs
from extant.guards.middleware import PgUsageStore, today_in_india


class Runtime(Protocol):
    @property
    def index_version(self) -> str: ...

    @property
    def cache(self) -> ExampleCache | None: ...

    @property
    def cap(self) -> int: ...

    def ask(
        self,
        question: str,
        entity: str | None,
        as_of: date | None,
        observe: Observe | None = None,
    ) -> AskResponse: ...

    def paragraph(self, para_id: str) -> catalog.ParagraphRecord | None: ...

    def entity_types(self) -> list[catalog.EntityTypeRecord]: ...  # raises a database error

    def readiness(self) -> Readiness: ...  # raises a database error if unreachable

    def answered(self, day: date) -> int: ...  # raises a database error if unreachable

    def close(self) -> None: ...


class PgRuntime:
    def __init__(
        self, serving: Serving, *, log: Callable[[Mapping[str, Any]], None] = logs.emit
    ) -> None:
        self.serving = serving
        self.log = log

    @property
    def index_version(self) -> str:
        return self.serving.schema

    @property
    def cache(self) -> ExampleCache | None:
        return self.serving.cache

    @property
    def cap(self) -> int:
        return self.serving.cap

    def ask(
        self,
        question: str,
        entity: str | None,
        as_of: date | None,
        observe: Observe | None = None,
    ) -> AskResponse:
        with service(self.serving, log=self.log) as s:
            return s.ask(question, entity=entity, as_of=as_of, observe=observe)

    def paragraph(self, para_id: str) -> catalog.ParagraphRecord | None:
        with connection(self.serving) as conn:
            found = catalog.fetch_paragraph(conn, para_id)
            if found is not None and found.resolved_from is not None:
                catalog.record_alias_hit(conn, para_id, found.para_id, self.serving.schema)
            return found

    def entity_types(self) -> list[catalog.EntityTypeRecord]:
        with connection(self.serving) as conn:
            return catalog.entity_taxonomy(conn)

    def readiness(self) -> Readiness:
        return self.serving.readiness()

    def answered(self, day: date | None = None) -> int:
        with self.serving.pool.connection() as conn:
            return PgUsageStore(conn, self.serving.cap).answered(day or today_in_india())

    def close(self) -> None:
        self.serving.close()
