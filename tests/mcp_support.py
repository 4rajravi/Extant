"""A backend for the MCP server with no database and no network.

``ask`` replays a recorded fixture through the real ``Service`` (see ``replay_support``);
the catalog methods return small fixed records. Every call is kept, so a test can check what
the server passed down and what it handed back.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from extant import catalog
from extant.agent.models import AskResponse
from extant.agent.runtime import Search
from extant.guards.middleware import MemoryUsageStore
from extant.mcp_server.backend import Searched
from tests import replay_support

VERSION = "idx_rbi_20261123_c23c1e8"
FIXTURE = "full_answer_third_person"
PARA = catalog.ParagraphRecord(
    para_id="rbi:md:2025:0143:v1#p12",
    doc_id="rbi:md:2025:0143:v1",
    doc_title="Reserve Bank of India (Commercial Banks - Dividends) Directions, 2025",
    paragraph_number="12",
    heading_path="Chapter III > Eligibility",
    body="12. A bank may declare dividends only if its net NPA ratio is below 6 per cent.",
    entity_scope=["cb"],
    in_force_from=date(2025, 11, 28),
    in_force_to=None,
    authority_rank=1,
    binding=True,
    source_url="https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=1",
    source_page=None,
)


@dataclass
class FakeBackend:
    store: MemoryUsageStore = field(default_factory=lambda: MemoryUsageStore(cap=1_000_000))
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    last_ask: AskResponse | None = None

    @property
    def index_version(self) -> str:
        return VERSION

    @property
    def entities(self) -> Mapping[str, str]:
        return {"cb": "Commercial Banks", "nbfc": "NBFCs", "nbfc_ul": "NBFC - Upper Layer"}

    def ask(self, question: str, entity: str | None, as_of: date | None) -> AskResponse:
        self.calls.append(("ask", (question, entity, as_of)))
        data = replay_support.load(FIXTURE)
        response = replay_support.serve(data, store=self.store).response
        self.last_ask = response
        return response

    def search(self, query: str, entity: str, as_of: date, top_n: int) -> Searched:
        self.calls.append(("search", (query, entity, as_of, top_n)))
        return Searched(query, (), Search(candidates=[], neighbours=[]))

    def paragraph(self, para_id: str) -> catalog.ParagraphRecord | None:
        self.calls.append(("paragraph", (para_id,)))
        return PARA if para_id == PARA.para_id else None

    def resolve(self, reference: str, entity: str, as_of: date) -> catalog.Resolution:
        self.calls.append(("resolve", (reference, entity, as_of)))
        return catalog.Resolution(
            reference=reference,
            canonical_ref="dbod.bp.bc.12/21.04.048/2014-15",
            as_of=as_of,
            status="not_found",
            message="Nothing in the index withdraws, consolidates or cites it.",
            withdrawals=[],
            consolidated_into=[],
            mentions=[],
        )

    def diff(self, doc_id: str, from_date: date, to_date: date) -> catalog.VersionDiff | None:
        self.calls.append(("diff", (doc_id, from_date, to_date)))
        if from_date > to_date:
            raise ValueError("from_date is after to_date")
        return None

    def applicable(
        self, entity: str, as_of: date, topic: str | None
    ) -> list[catalog.DirectionRecord]:
        self.calls.append(("applicable", (entity, as_of, topic)))
        return []

    def manifest(self) -> dict[str, Any]:
        return {"index_version": VERSION, "build": {}, "coverage": {}}

    def entity_types(self) -> list[catalog.EntityTypeRecord]:
        return [
            catalog.EntityTypeRecord(code="cb", label="Commercial Banks", parent=None, children=[])
        ]
