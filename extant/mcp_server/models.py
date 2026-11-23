"""What the MCP tools return, beyond the models the layers below already define.

``ask`` returns the Phase 4 ``AskResponse`` itself and ``search`` returns the agent's own
``Candidate`` records, so a client sees the same objects the service and the graph do. The
envelopes here add only what a client needs to read a result correctly: the build it came
from, the filters that were applied (with the defaults made explicit), and, on every tool but
``ask``, that the result is unverified.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from extant.agent.models import Candidate
from extant.catalog import DirectionRecord, ParagraphRecord, Resolution, VersionDiff

UNVERIFIED = (
    "Unverified: paragraphs and records as the index holds them. Extant has checked no claim"
    " here, and prose a client writes from them is outside its verification boundary. Only"
    " the ask tool returns verified claims."
)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Filters(_Model):
    """The prefilter a paragraph-returning tool applied. Both parts always apply."""

    entity: str
    as_of: date
    as_of_defaulted: bool  # true when the caller gave no date and today in India was used


class _Unverified(_Model):
    index_version: str
    verified: Literal[False] = False
    verification: str = UNVERIFIED


class SearchResult(_Unverified):
    query: str  # as searched: redacted if it carried an identifier
    redactions: list[str]
    filters: Filters
    results: list[Candidate]  # ranked, best first
    context: list[Candidate]  # neighbouring paragraphs of the ranked ones, same filters
    degraded: list[str]  # "lexical_only" when the embedding call failed


class ParagraphResult(_Unverified):
    found: bool
    paragraph: ParagraphRecord | None
    message: str


class ResolutionResult(_Unverified):
    filters: Filters
    resolution: Resolution


class DiffResult(_Unverified):
    found: bool
    diff: VersionDiff | None
    message: str


class ApplicableResult(_Unverified):
    filters: Filters
    topic: str | None
    count: int
    directions: list[DirectionRecord]
    note: str
