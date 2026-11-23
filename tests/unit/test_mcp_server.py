"""The MCP surface's contract: its tools, their inputs and outputs, the verification boundary
in every description, no way around the prefilter, the cap, and the cache hints."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import anyio
import pytest
from mcp import Client

from extant.agent.models import AskResponse
from extant.guards.middleware import MemoryUsageStore, today_in_india
from extant.mcp_server.models import (
    ApplicableResult,
    DiffResult,
    ParagraphResult,
    ResolutionResult,
    SearchResult,
)
from extant.mcp_server.server import ENTITY_TYPES_URI, MANIFEST_URI, build_server
from tests import replay_support
from tests.mcp_support import FIXTURE, PARA, VERSION, FakeBackend

# Every input any tool accepts. A new parameter fails here until someone has looked at it:
# none may switch off the entity or the date filter.
ALLOWED = {
    "ask": {"question", "entity", "as_of"},
    "search": {"query", "entity", "as_of", "top_n"},
    "fetch_paragraph": {"para_id"},
    "resolve_circular": {"reference", "entity", "as_of"},
    "diff_versions": {"direction", "from_date", "to_date"},
    "list_applicable": {"entity", "as_of", "topic"},
}
SEARCHING = ("search", "resolve_circular", "list_applicable")


def run[T](backend: FakeBackend, body: Callable[[Client], Awaitable[T]]) -> T:
    async def go() -> T:
        async with Client(build_server(backend)) as client:
            return await body(client)

    return anyio.run(go)


def call(backend: FakeBackend, tool: str, args: dict[str, Any]) -> Any:
    return run(backend, lambda c: c.call_tool(tool, args))


def tools(backend: FakeBackend | None = None) -> dict[str, Any]:
    listed = run(backend or FakeBackend(), lambda c: c.list_tools())
    return {t.name: t for t in listed.tools}


def test_exactly_the_six_tools_and_two_resources() -> None:
    assert set(tools()) == set(ALLOWED)
    listed = run(FakeBackend(), lambda c: c.list_resources())
    assert {str(r.uri) for r in listed.resources} == {MANIFEST_URI, ENTITY_TYPES_URI}


def test_no_parameter_beyond_the_allowlist() -> None:
    for name, tool in tools().items():
        assert set(tool.input_schema["properties"]) == ALLOWED[name], name


def test_every_search_requires_an_entity() -> None:
    for name in SEARCHING:
        assert "entity" in tools()[name].input_schema["required"], name


def test_extra_arguments_cannot_widen_the_filter() -> None:
    backend = FakeBackend()
    args = {"query": "dividend", "entity": "cb", "validity": "any", "include_untrusted": True}
    result = call(backend, "search", args)
    if not result.is_error:  # ignored, then: what reached the layer below is unchanged
        assert backend.calls == [("search", ("dividend", "cb", today_in_india(), 10))]


def test_every_description_states_the_verification_boundary() -> None:
    listed = tools()
    assert "only Extant tool whose output is verified" in (listed["ask"].description or "")
    for name in set(ALLOWED) - {"ask"}:
        assert "UNVERIFIED" in (listed[name].description or ""), name
    assert "outside Extant's verification boundary" in (listed["search"].description or "")


def test_every_tool_declares_an_output_schema() -> None:
    for name, tool in tools().items():
        assert tool.output_schema, name


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search", {"query": "dividend", "entity": "CB"}),  # not a code
        ("search", {"query": "dividend", "entity": "sfb"}),  # not in this build
        ("search", {"query": "dividend", "entity": "cb", "top_n": 0}),
        ("search", {"query": "dividend", "entity": "cb", "top_n": 21}),
        ("search", {"query": "dividend", "entity": "cb", "as_of": "20-11-2026"}),
        ("search", {"query": "x", "entity": "cb"}),
        ("search", {"query": "dividend"}),  # no entity
        ("fetch_paragraph", {"para_id": "p12"}),
        ("fetch_paragraph", {"para_id": "rbi:md:2025:0143:v1#p12; DROP TABLE x"}),
        (
            "diff_versions",
            {"direction": "0143", "from_date": "2026-01-01", "to_date": "2026-02-01"},
        ),
        (
            "diff_versions",
            {"direction": "rbi:md:2025:0143", "from_date": "2026-02-01", "to_date": "2026-01-01"},
        ),
        ("list_applicable", {"entity": "cb", "topic": "x"}),
        ("ask", {"question": "?"}),
        ("ask", {"question": "Can a bank declare dividends?", "entity": "sfb"}),
    ],
)
def test_bad_input_is_a_tool_error_and_never_reaches_the_layer_below_unchecked(
    tool: str, args: dict[str, Any]
) -> None:
    backend = FakeBackend()
    result = call(backend, tool, args)
    assert result.is_error
    assert all(name == "diff" for name, _ in backend.calls)  # only diff's own check is below


def test_outputs_validate_against_their_models_and_carry_the_build() -> None:
    backend = FakeBackend()
    cases = [
        ("search", {"query": "dividend", "entity": "cb"}, SearchResult),
        ("fetch_paragraph", {"para_id": PARA.para_id}, ParagraphResult),
        (
            "resolve_circular",
            {"reference": "DBOD.No.BP.BC.12/21.04.048/2014-15", "entity": "cb"},
            ResolutionResult,
        ),
        (
            "diff_versions",
            {"direction": "rbi:md:2025:0143", "from_date": "2026-01-01", "to_date": "2026-02-01"},
            DiffResult,
        ),
        ("list_applicable", {"entity": "nbfc_ul", "topic": "gold loans"}, ApplicableResult),
    ]
    for tool, args, model in cases:
        result = call(backend, tool, args)
        assert not result.is_error, (tool, result.content)
        parsed = model.model_validate(result.structured_content)
        assert parsed.index_version == VERSION
        assert parsed.verified is False
        assert json.loads(result.content[0].text) == result.structured_content


def test_a_missing_date_is_today_in_india_and_says_so() -> None:
    result = call(FakeBackend(), "list_applicable", {"entity": "cb"})
    filters = result.structured_content["filters"]
    assert filters == {
        "entity": "cb",
        "as_of": today_in_india().isoformat(),
        "as_of_defaulted": True,
    }
    given = call(FakeBackend(), "list_applicable", {"entity": "cb", "as_of": "2026-01-15"})
    assert given.structured_content["filters"]["as_of_defaulted"] is False


def test_a_paragraph_that_is_not_there_is_found_false_not_an_error() -> None:
    result = call(FakeBackend(), "fetch_paragraph", {"para_id": "rbi:md:2025:0143:v1#p99"})
    assert not result.is_error
    assert result.structured_content["found"] is False


def test_ask_through_mcp_is_the_service_response_unchanged() -> None:
    backend = FakeBackend()
    result = call(backend, "ask", {"question": "Can a commercial bank declare dividends?"})
    assert backend.last_ask is not None
    assert result.structured_content == backend.last_ask.model_dump(mode="json")
    assert AskResponse.model_validate(result.structured_content) == backend.last_ask
    assert backend.last_ask.result.outcome == "answer"


def test_the_daily_cap_binds_ask_through_mcp() -> None:
    store = MemoryUsageStore(cap=3)
    backend = FakeBackend(store=store)
    # The replayed service runs on its fixture's own day: that day's answers are spent.
    day = date.fromisoformat(replay_support.load(FIXTURE)["today"])
    store.days[day] = {"requests": 3, "answered": 3, "model_calls": 0, "model_eur": 0.0}
    result = call(backend, "ask", {"question": "Can a commercial bank declare dividends?"})
    body = result.structured_content["result"]
    assert (body["outcome"], body["reason"]) == ("refusal", "cap_reached")
    assert store.calls == []  # no model call was made


def test_list_and_read_results_carry_their_cache_hints() -> None:
    async def body(c: Client) -> list[tuple[int | None, str | None]]:
        out = []
        for listed in (await c.list_tools(), await c.list_resources()):
            out.append((listed.ttl_ms, listed.cache_scope))
        read = await c.read_resource(MANIFEST_URI)
        out.append((read.ttl_ms, read.cache_scope))
        return out

    assert run(FakeBackend(), body) == [
        (86_400_000, "public"),
        (86_400_000, "public"),
        (900_000, "public"),
    ]


def test_the_manifest_names_the_build_every_tool_reports() -> None:
    async def body(c: Client) -> tuple[str, str]:
        read = await c.read_resource(MANIFEST_URI)
        searched = await c.call_tool("search", {"query": "dividend", "entity": "cb"})
        return json.loads(read.contents[0].text)["index_version"], searched.structured_content[
            "index_version"
        ]

    manifest, tool = run(FakeBackend(), body)
    assert manifest == tool == VERSION
