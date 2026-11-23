"""Extant over MCP: six tools and two resources on the configured build.

Every tool is a typed wrapper over one function in the layers below (see ``backend``): it
validates its input, calls that function, and shapes the result. It holds no agent logic.
``ask`` is the only tool whose output is verified; every other tool says it is not, in its
description and in its result.

There is no parameter anywhere here that disables the entity or the as-of date prefilter.
Every tool that returns paragraphs found by search (``search``, ``resolve_circular``,
``list_applicable``) requires an entity code and applies a date, today in India when none is
given, and reports both back. The two that look a record up by its identifier
(``fetch_paragraph``, ``diff_versions``) return that record with its scope and validity, so
the caller can see whether it applies.

Protocol 2026-07-28 is stateless: no session, no initialize handshake, every request carries
its own protocol version. This server keeps no state between requests either, so any number
of replicas can serve the same clients.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Final

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from mcp_types.methods import CacheableMethod
from pydantic import Field

import extant
from extant.agent.models import AskResponse
from extant.core.identity import ENTITY_CODE_RE, PARA_ID_RE
from extant.guards.middleware import today_in_india
from extant.mcp_server.backend import Backend
from extant.mcp_server.models import (
    ApplicableResult,
    DiffResult,
    Filters,
    ParagraphResult,
    ResolutionResult,
    SearchResult,
)

MANIFEST_URI: Final = "corpus://manifest"
ENTITY_TYPES_URI: Final = "corpus://entity_types"

_MINUTE = 60_000
_DAY = 24 * 60 * _MINUTE
# The lists are the same for every client (there is no per client state or authorisation,
# so "public") and change only when this code is deployed, which restarts the server: a
# day. A stale list after a deploy costs a client one failed call, then a refetch. A
# resource read names the build being served, which changes when a new build is rolled out
# (also a restart); fifteen minutes bounds how long a client may show the old one, and every
# tool result carries its own index_version, so a client can always tell.
CACHE_HINTS: Final[dict[CacheableMethod, CacheHint]] = {
    "server/discover": CacheHint(ttl_ms=_DAY, scope="public"),
    "tools/list": CacheHint(ttl_ms=_DAY, scope="public"),
    "prompts/list": CacheHint(ttl_ms=_DAY, scope="public"),
    "resources/list": CacheHint(ttl_ms=_DAY, scope="public"),
    "resources/templates/list": CacheHint(ttl_ms=_DAY, scope="public"),
    "resources/read": CacheHint(ttl_ms=15 * _MINUTE, scope="public"),
}

INSTRUCTIONS: Final = """\
Extant answers questions about the Reserve Bank of India's Master Directions with verified
citations, or says why it cannot. Use `ask` for an answer you will show to a person: each of
its claims cites paragraphs, with quotes checked verbatim against them, and it refuses rather
than guess. The other tools return paragraphs and records from the index unverified; if you
write prose from them, that prose is outside Extant's verification, so say so or quote them
whole. Every search is filtered to one entity type and one date. Entity codes are in
corpus://entity_types; the build and its coverage are in corpus://manifest."""

_ENTITY_DOC = (
    "Entity type code, e.g. 'cb' (commercial banks), 'nbfc', 'ucb'; the full list is the"
    " resource corpus://entity_types. Codes match exactly: an NBFC subtype such as 'nbfc_ul'"
    " does not include paragraphs tagged only 'nbfc', which apply to every NBFC, so to cover"
    " both call once with each."
)
_AS_OF_DOC = "The date the provisions must be in force on, YYYY-MM-DD. Default: today in India."

Entity = Annotated[str, Field(pattern=f"^{ENTITY_CODE_RE.pattern}$", description=_ENTITY_DOC)]
AsOf = Annotated[date | None, Field(description=_AS_OF_DOC)]

ASK = """\
Answer a question about RBI Master Directions for one kind of regulated entity on one date.

This is the only Extant tool whose output is verified. The result is one of:
- answer: claims, each citing paragraphs, with every quote checked verbatim against the
  paragraph it cites and every number checked against the quotes;
- refusal: a reason (out_of_scope, computation_requested, entity_required, no_support_found,
  superseded_only, non_binding_support_only, verification_failed, cap_reached) and the
  nearest paragraphs it found but could not stand behind;
- retrieval_only: a model was unavailable, so the provisions are returned whole, with no prose;
- unavailable: the index could not be reached; any examples shown are not answers to this
  question.
Show the claims and citations as given. Anything you add around them is your text, not
Extant's, and is outside its verification. It does not compute (eligibility, ratios, sums).

Each call makes model calls and counts against a daily limit; at the limit the result is a
refusal with reason cap_reached. Identifiers in the question (PAN, account numbers and the
like) are removed before anything else sees it, and the result lists what was removed."""

SEARCH = """\
Search the index for paragraphs in force on a date for one entity type, best first.

UNVERIFIED. The results are paragraphs as published, ranked by hybrid lexical and semantic
retrieval: nothing in them has been checked against your question. If you, or the model you
run, write prose from these results, that prose is outside Extant's verification boundary:
it may misread, mix versions or overstate, and Extant cannot tell. For an answer to show a
person, call ask. Use search to find paragraph identifiers and the wording of a provision.

The entity and the date always filter the search; neither can be switched off. A query that
names a circular (e.g. DBOD.No.BP.BC.12/21.04.048/2014-15) returns the in-force paragraphs
that cite it. Identifiers in the query are removed before it is embedded."""

FETCH = """\
Fetch one paragraph by its identifier (e.g. rbi:md:2025:0143:v1#p12), with its direction,
the entity types it applies to, the dates it is in force (in_force_to is the first day it no
longer applies; null while in force), its authority and its published source.

UNVERIFIED: the paragraph as the index holds it. An identifier retired by a later reparse
resolves to the paragraph that replaced it, and the result says so. This is a lookup by
identifier, so it returns the paragraph whatever its scope or dates; check entity_scope and
the in-force dates before relying on it."""

RESOLVE = """\
Resolve an RBI circular reference (e.g. DBOD.No.BP.BC.12/21.04.048/2014-15) as of a date:
whether RBI withdrew it and from when, which Master Direction consolidated it, and which
paragraphs in force on that date for the entity type cite it.

UNVERIFIED: records from RBI's withdrawal lists and consolidation annexes as the index holds
them. A withdrawal or consolidation holds for every entity; the citing paragraphs are
filtered by the entity and the date. Makes no model or embedding call. A withdrawn circular's
own text is not in the index."""

DIFF = """\
Compare the versions of one Master Direction in force on two dates, paragraph by paragraph:
which paragraphs were added, removed or changed, with both texts.

UNVERIFIED: a mechanical comparison of the stored text. The index holds the directions from
the November 2025 consolidation onwards; a version RBI published only as a PDF is not in it.
So the status is "changed", "unchanged" (the same version was in force on both dates, or two
versions with the same text), or "not_covered" (a date the index holds no version for), and
the message names any missing version in or next to the span. Never read not_covered as "no
change". Pass any identifier of the direction: rbi:md:2025:0143, a version, or a paragraph."""

APPLICABLE = """\
List the Master Directions with at least one paragraph in force on a date for one entity
type, by title; with a topic, only those whose in-force text matches it, best match first
(lexical, no embedding call).

UNVERIFIED: a listing from the index's scope and date tags, which are applied per paragraph:
a direction appears if any paragraph in it applies, not because all of it does. The entity
and the date always filter it."""


def _today(as_of: date | None) -> tuple[date, bool]:
    return (as_of, False) if as_of is not None else (today_in_india(), True)


def _call[T](fn: Callable[[], T]) -> T:
    """A ValueError from the layer below is the caller's input: report it as a tool error."""
    try:
        return fn()
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def build_server(backend: Backend) -> MCPServer:
    server = MCPServer(
        name="extant",
        title="Extant",
        description="Verified answers over RBI Master Directions, or a stated refusal.",
        version=extant.__version__,
        instructions=INSTRUCTIONS,
        cache_hints=CACHE_HINTS,
        subscriptions=False,  # nothing here changes while the server runs
    )

    def entity_code(code: str) -> str:
        if code not in backend.entities:
            raise ToolError(
                f"unknown entity type {code!r} in {backend.index_version}; one of"
                f" {', '.join(backend.entities)} (see {ENTITY_TYPES_URI})"
            )
        return code

    def filters(entity: str, as_of: date | None) -> Filters:
        day, defaulted = _today(as_of)
        return Filters(entity=entity_code(entity), as_of=day, as_of_defaulted=defaulted)

    lookup = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

    @server.tool(
        name="ask",
        title="Ask (verified answer)",
        description=ASK,
        annotations=ToolAnnotations(
            read_only_hint=True, idempotent_hint=False, open_world_hint=False
        ),
    )
    def ask(
        question: Annotated[str, Field(min_length=3, max_length=2000)],
        entity: Annotated[
            str | None,
            Field(
                pattern=f"^{ENTITY_CODE_RE.pattern}$",
                description=_ENTITY_DOC + " Optional: without one, the question must make the"
                " entity clear, or the result may be a refusal with reason entity_required.",
            ),
        ] = None,
        as_of: Annotated[
            date | None,
            Field(
                description="The date the answer must hold on, YYYY-MM-DD. Default: today in"
                " India, and the answer then carries the as_of_assumed caveat."
            ),
        ] = None,
    ) -> AskResponse:
        if entity is not None:
            entity_code(entity)
        return backend.ask(question, entity, as_of)

    @server.tool(name="search", title="Search (unverified)", description=SEARCH, annotations=lookup)
    def search(
        query: Annotated[str, Field(min_length=3, max_length=500)],
        entity: Entity,
        as_of: AsOf = None,
        top_n: Annotated[int, Field(ge=1, le=20, description="Ranked results to return.")] = 10,
    ) -> SearchResult:
        f = filters(entity, as_of)
        found = _call(lambda: backend.search(query, f.entity, f.as_of, top_n))
        return SearchResult(
            index_version=backend.index_version,
            query=found.query,
            redactions=list(found.redactions),
            filters=f,
            results=found.search.candidates,
            context=found.search.neighbours,
            degraded=found.search.degraded,
        )

    @server.tool(
        name="fetch_paragraph", title="Fetch a paragraph", description=FETCH, annotations=lookup
    )
    def fetch_paragraph(
        para_id: Annotated[
            str,
            Field(
                pattern=f"^{PARA_ID_RE.pattern}$",
                description="Paragraph identifier, <doc_id>#<anchor>, as search and ask return.",
            ),
        ],
    ) -> ParagraphResult:
        p = _call(lambda: backend.paragraph(para_id))
        if p is None:
            message = f"No paragraph {para_id} in {backend.index_version}."
        elif p.resolved_from is not None:
            message = f"{para_id} was retired ({p.resolution_reason}); it is now {p.para_id}."
        else:
            message = "Found."
        return ParagraphResult(
            index_version=backend.index_version, found=p is not None, paragraph=p, message=message
        )

    @server.tool(
        name="resolve_circular",
        title="Resolve a circular reference",
        description=RESOLVE,
        annotations=lookup,
    )
    def resolve_circular(
        reference: Annotated[
            str,
            Field(
                min_length=5,
                max_length=200,
                description="The circular's reference number as RBI prints it.",
            ),
        ],
        entity: Entity,
        as_of: AsOf = None,
    ) -> ResolutionResult:
        f = filters(entity, as_of)
        resolution = _call(lambda: backend.resolve(reference, f.entity, f.as_of))
        return ResolutionResult(
            index_version=backend.index_version, filters=f, resolution=resolution
        )

    @server.tool(
        name="diff_versions",
        title="Diff a direction between two dates",
        description=DIFF,
        annotations=lookup,
    )
    def diff_versions(
        direction: Annotated[
            str,
            Field(
                pattern=r"^rbi:[a-z]+:\d{4}:\d{4}(:v\d+)?(#\S+)?$",
                description="The direction, e.g. rbi:md:2025:0143, or any version or paragraph"
                " identifier of it.",
            ),
        ],
        from_date: Annotated[date, Field(description="The earlier date, YYYY-MM-DD.")],
        to_date: Annotated[date, Field(description="The later date, YYYY-MM-DD.")],
    ) -> DiffResult:
        d = _call(lambda: backend.diff(direction, from_date, to_date))
        message = d.message if d else f"No direction {direction} in {backend.index_version}."
        return DiffResult(
            index_version=backend.index_version, found=d is not None, diff=d, message=message
        )

    @server.tool(
        name="list_applicable",
        title="Directions in force for an entity",
        description=APPLICABLE,
        annotations=lookup,
    )
    def list_applicable(
        entity: Entity,
        as_of: AsOf = None,
        topic: Annotated[
            str | None,
            Field(
                min_length=2,
                max_length=200,
                description="A few words, e.g. 'gold loans'. Any word may match.",
            ),
        ] = None,
    ) -> ApplicableResult:
        f = filters(entity, as_of)
        found = _call(lambda: backend.applicable(f.entity, f.as_of, topic))
        return ApplicableResult(
            index_version=backend.index_version,
            filters=f,
            topic=topic,
            count=len(found),
            directions=found,
            note="Listed if any paragraph applies to the entity on the date, not all of it.",
        )

    @server.resource(
        MANIFEST_URI,
        name="manifest",
        title="Build manifest and coverage",
        description="The build being served (index_version, as every tool result reports it),"
        " how it was built, and what the corpus covers and does not.",
        mime_type="application/json",
    )
    def manifest() -> str:
        return json.dumps(backend.manifest(), default=str, indent=1)

    @server.resource(
        ENTITY_TYPES_URI,
        name="entity_types",
        title="Entity type codes",
        description="Every entity type code the tools accept, its label, and the one"
        " hierarchy: NBFC subtypes (nbfc_*) under nbfc. Codes match exactly in search.",
        mime_type="application/json",
    )
    def entity_types() -> str:
        rows: list[dict[str, Any]] = [e.model_dump() for e in backend.entity_types()]
        return json.dumps({"index_version": backend.index_version, "entity_types": rows}, indent=1)

    del ask, search, fetch_paragraph, resolve_circular, diff_versions, list_applicable
    del manifest, entity_types
    return server
