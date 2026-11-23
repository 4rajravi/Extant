"""Building blocks for the agent tests: candidates, a scripted index, scripted models."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from extant.agent.graph import ask, build
from extant.agent.llm import FakeModel
from extant.agent.models import (
    AgentState,
    Candidate,
    ComposerClaim,
    ComposerOutput,
    JudgeOutput,
    ParseOutput,
    ReferenceRecord,
)
from extant.agent.runtime import Deps, Search

TODAY = date(2026, 11, 22)
ENTITIES = {
    "cb": "Commercial Banks",
    "ucb": "Urban Co-operative Banks",
    "sfb": "Small Finance Banks",
}


def cand(
    n: int,
    body: str = "A bank shall maintain a minimum capital ratio of 9 per cent at all times.",
    *,
    doc: str = "rbi:md:2025:0001:v1",
    scope: tuple[str, ...] = ("cb",),
    valid_from: date = date(2025, 1, 1),
    valid_to: date | None = None,
    authority_rank: int = 30,
    asked_for: date = TODAY,
    origin: Literal["ranked", "expanded", "reference", "probe", "diff"] = "ranked",
    rank: int | None = None,
) -> Candidate:
    expanded = origin == "expanded"
    return Candidate(
        para_id=f"{doc}#p{n}",
        doc_id=doc,
        doc_title="Zebra Directions, 2025",
        paragraph_number=str(n),
        heading_path="Chapter I > Capital",
        body=body,
        ordinal=n,
        rank=None if expanded else (rank if rank is not None else n),
        fusion_score=None if expanded else 1.0 / (60 + n),
        lexical_rank=None if expanded else n,
        dense_rank=None if expanded else n,
        expanded=expanded,
        origin=origin,
        entity_scope=scope,
        valid_from=valid_from,
        valid_to=valid_to,
        authority_rank=authority_rank,
        binding=authority_rank not in (60, 70),
        source_url="https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=1",
        asked_for=asked_for,
    )


@dataclass
class ScriptedIndex:
    """An index that returns what the test says, and counts what was asked of it."""

    ranked: list[Candidate] = field(default_factory=list)
    neighbours: list[Candidate] = field(default_factory=list)
    probe: list[Candidate] = field(default_factory=list)
    refs: tuple[list[Candidate], list[ReferenceRecord]] = field(default_factory=lambda: ([], []))
    other_version: Candidate | None = None
    embedding_tokens: int = 12
    codes: tuple[str, ...] = tuple(sorted(ENTITIES))
    calls: list[str] = field(default_factory=list)

    @property
    def embedding_model(self) -> str:
        return "test/hash-256"

    def entity_codes(self) -> tuple[str, ...]:
        return self.codes

    def search(
        self,
        query: str,
        entity_codes: tuple[str, ...],
        as_of: date,
        *,
        validity: Literal["in_force", "any"],
        top_n: int,
    ) -> Search:
        self.calls.append(f"search:{validity}")
        if validity == "any":
            return Search(candidates=list(self.probe), neighbours=[])
        return Search(
            candidates=list(self.ranked),
            neighbours=list(self.neighbours),
            embedding_calls=1,
            embedding_tokens=self.embedding_tokens,
        )

    def reference(
        self, query: str, entity_codes: tuple[str, ...], as_of: date
    ) -> tuple[list[Candidate], list[ReferenceRecord]]:
        self.calls.append("reference")
        return self.refs

    def version_at(self, para_id: str, day: date) -> Candidate | None:
        self.calls.append("version_at")
        return self.other_version


Responder = Callable[[type[BaseModel], str, str], BaseModel]


def parse_says(**fields: Any) -> FakeModel:
    defaults: dict[str, Any] = {
        "in_scope": True,
        "computation_requested": False,
        "query_kind": "applicability",
    }
    out = ParseOutput(**{**defaults, **fields})
    return FakeModel(
        lambda schema, s, u: out, name="claude-haiku-5-5", input_tokens=500, output_tokens=40
    )


def composer_says(
    *claims: tuple[str, Sequence[int], Sequence[str]],
    insufficient: bool = False,
    missing: str | None = None,
) -> FakeModel:
    out = ComposerOutput(
        claims=[
            ComposerClaim(text=t, candidate_indices=list(i), quotes=list(q)) for t, i, q in claims
        ],
        insufficient=insufficient,
        missing=missing,
    )
    return FakeModel(
        lambda schema, s, u: out, name="claude-opus-5-5", input_tokens=3200, output_tokens=400
    )


def judge_says(supported: bool = True) -> FakeModel:
    return FakeModel(
        lambda schema, s, u: JudgeOutput(supported=supported),
        name="claude-haiku-5-5",
        input_tokens=900,
        output_tokens=5,
    )


def run_graph(
    question: str,
    *,
    index: ScriptedIndex | None = None,
    parse: FakeModel | None = None,
    compose: FakeModel | None = None,
    judge: FakeModel | None = None,
    entity: str | None = "cb",
    as_of: date | None = TODAY,
) -> AgentState:
    models = {k: v for k, v in (("parse", parse), ("compose", compose), ("judge", judge)) if v}
    deps = Deps(models=models, index=index or ScriptedIndex(), entities=ENTITIES)
    return ask(
        build(deps),
        question,
        index_version="idx_rbi_20261120_38ede3c",
        entity=entity,
        as_of=as_of,
        today=TODAY,
    )
