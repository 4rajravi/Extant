"""The MCP tools on ``PgBackend`` against a real build of the synthetic corpus, as the read
only role. The embedder raises on any call: only ``search`` may embed, so every other tool
reaching its assertions proves it made no embedding call (and none of them has a model)."""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any

import anyio
import psycopg
import pytest
from mcp import Client

from extant.agent.serving import Serving, make_pool
from extant.core.identity import make_schema_name
from extant.core.schema import drop, schema_exists
from extant.index.assemble import assemble
from extant.index.embed import Embedded, HashEmbedder, Kind, embed_missing, plan
from extant.mcp_server.backend import PgBackend
from extant.mcp_server.server import MANIFEST_URI, build_server
from tests.integration.synthetic import D1V1, D1V2, D4, REF_SURFACE, build_inputs

pytestmark = pytest.mark.integration


class Refuses:
    identity = HashEmbedder().identity

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        raise AssertionError("an embedding call was made")


@pytest.fixture(scope="module")
def schema(writer_dsn: str, bootstrapped: None) -> Iterator[str]:
    inputs = build_inputs()
    name = make_schema_name("test", date.today(), secrets.token_hex(4))
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        e = HashEmbedder()
        todo = plan(
            c, {x.content_hash: x.embed_text for x in inputs.chunks}, e.identity, eur_per_usd=0.9
        )
        embed_missing(c, e, todo.misses)
        assemble(c, name, inputs, e.identity, baseline=None)
    yield name
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        if schema_exists(c, name):
            drop(c, name)


@pytest.fixture
def pools(reader_dsn: str) -> Iterator[list[Any]]:
    made: list[Any] = []
    yield made
    for pool in made:
        pool.close()


def _backend(reader_dsn: str, schema: str, embedder: Any, pools: list[Any]) -> PgBackend:
    pool = make_pool(reader_dsn)
    pools.append(pool)
    return PgBackend(
        Serving(
            pool=pool,
            schema=schema,
            cap=1,
            embedder=embedder,
            models=lambda store: {},
            eur_per_usd=0.9,
            cache=None,
        )
    )


def _call(backend: PgBackend, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        async with Client(build_server(backend)) as client:
            result = await client.call_tool(tool, args)
            assert not result.is_error, result.content
            out: dict[str, Any] = result.structured_content or {}
            return out

    return anyio.run(go)


def test_lookups_make_no_embedding_call(reader_dsn: str, schema: str, pools: list[Any]) -> None:
    b = _backend(reader_dsn, schema, Refuses(), pools)
    para = _call(b, "fetch_paragraph", {"para_id": f"{D1V1.doc_id}#p1"})
    assert para["paragraph"]["in_force_to"] == "2026-01-01"
    assert para["index_version"] == schema

    resolved = _call(
        b, "resolve_circular", {"reference": REF_SURFACE, "entity": "ucb", "as_of": "2026-01-15"}
    )
    assert resolved["resolution"]["status"] == "withdrawn"
    assert resolved["resolution"]["consolidated_into"] == [D4.doc_id]

    diff = _call(
        b,
        "diff_versions",
        {"direction": "rbi:md:2025:0001", "from_date": "2025-06-01", "to_date": "2026-06-01"},
    )
    assert (diff["diff"]["from_version"], diff["diff"]["to_version"]) == (
        D1V1.doc_id,
        D1V2.doc_id,
    )

    listed = _call(b, "list_applicable", {"entity": "cb", "as_of": "2026-06-01"})
    assert [d["doc_id"] for d in listed["directions"]] == [D4.doc_id, D1V2.doc_id]


def test_search_applies_the_entity_and_the_date(
    reader_dsn: str, schema: str, pools: list[Any]
) -> None:
    b = _backend(reader_dsn, schema, HashEmbedder(), pools)
    found = _call(b, "search", {"query": "dividend records", "entity": "cb", "as_of": "2025-06-01"})
    assert found["results"]
    for c in found["results"] + found["context"]:
        assert "cb" in c["entity_scope"]
        assert c["valid_from"] <= "2025-06-01"
        assert c["valid_to"] is None or c["valid_to"] > "2025-06-01"
    assert found["filters"] == {"entity": "cb", "as_of": "2025-06-01", "as_of_defaulted": False}


def test_the_manifest_resource_names_the_build_the_tools_report(
    reader_dsn: str, schema: str, pools: list[Any]
) -> None:
    async def go() -> str:
        async with Client(build_server(_backend(reader_dsn, schema, Refuses(), pools))) as client:
            read = await client.read_resource(MANIFEST_URI)
            return str(json.loads(read.contents[0].text)["index_version"])  # type: ignore[union-attr]

    assert anyio.run(go) == schema
