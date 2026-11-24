"""The API's request and envelopes. The answer itself is the Phase 4 ``AskResponse``, the
citation the catalog's ``ParagraphRecord``, the examples the Phase 5 ``CachedExample``: none is
redefined here. What is added is what a client needs around them, the build first."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from extant.agent.models import AskResponse, CachedExample
from extant.catalog import EntityTypeRecord, ParagraphRecord


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AskRequest(_Model):
    question: str = Field(min_length=3, max_length=2000)
    entity: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
        description="Entity type code. Optional: without one the graph decides from the"
        " question, or refuses with entity_required.",
    )
    as_of: date | None = Field(
        default=None,
        description="The date the answer must hold on. Optional: the graph defaults to today"
        " in India and marks the answer as_of_assumed.",
    )


class ParagraphResponse(_Model):
    index_version: str
    requested: str  # the id as asked for
    resolved_through_alias: bool
    note: str | None  # says so when the id was retired: a stale citation is visibly stale
    paragraph: ParagraphRecord


class ExamplesResponse(_Model):
    index_version: str  # the build this process serves
    examples_index_version: str | None  # the build the examples were answered on
    stale: bool  # true when those differ: served anyway, and said so
    built_on: date | None
    message: str
    examples: list[CachedExample]


class EntityTypes(_Model):
    index_version: str
    entity_types: list[EntityTypeRecord]  # in the catalog's order


class StreamFinal(_Model):
    """The last event of ``POST /ask/stream``: the same ``AskResponse`` ``POST /ask`` returns,
    and the Retry-After that ``POST /ask`` would have sent as a header (an HTTP date, only on
    ``cap_reached``), since a stream's headers are gone before the cap is known."""

    response: AskResponse
    retry_after: str | None


class StreamFailure(_Model):
    """The last event when the request failed after the stream began: what ``POST /ask``
    would have answered with a 500. Nothing was answered."""

    detail: str
    index_version: str


class DailyCap(_Model):
    day: date  # a calendar day in India, as the cap counts it
    cap: int
    answered: int
    remaining: int


class Health(_Model):
    status: Literal["ready", "not_ready"]
    index_version: str
    database: Literal["reachable", "unreachable"]
    manifest_ok: bool
    problems: list[str]
    warnings: list[str]
    daily_cap: DailyCap | None


class Live(_Model):
    status: Literal["alive"] = "alive"
    index_version: str


class ErrorBody(_Model):
    """A rejection or a failure: never an answer. Rate limit, unknown citation, outage."""

    detail: str
    index_version: str
    retry_after_seconds: int | None = None
