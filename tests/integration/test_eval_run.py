"""A full retrieval eval run against a scratch build: no network, loud misses, counted tests.

The synthetic corpus from the Phase 2 tests is assembled into a scratch schema with hash
vectors, a handful of eval queries with obvious gold paragraphs are written for it, and their
query vectors are committed to a temporary cache. The run then goes through ``evals.retrieval``
exactly as CI does, with the network cut at the transport layer.
"""

from __future__ import annotations

import json
import secrets
import socket
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from evals.arms import ARMS, BY_NAME
from evals.query_vectors import CachedQueryEmbedder, MissingQueryVectorError, refresh
from evals.retrieval import run
from evals.sets import EvalQuery, EvalSet
from extant.core.identity import make_schema_name
from extant.core.schema import drop, schema_exists
from extant.index.assemble import assemble
from extant.index.embed import HashEmbedder, embed_missing, plan
from tests.integration.synthetic import D1V1, D1V2, D2, D3, D4, REF_SURFACE, build_inputs

pytestmark = pytest.mark.integration


def query(
    qid: str, text: str, codes: tuple[str, ...], as_of: date, gold: str, **kw: Any
) -> EvalQuery:
    doc = gold.split("#")[0]
    return EvalQuery(
        set=kw.get("set", "temporal"), query_id=qid, split=kw.get("split", "dev"), group=qid,
        text=text, entity_codes=codes, as_of=as_of, level="paragraph", gold_para_ids=(gold,),
        gold_doc_ids=(doc,), published_on=as_of, holdout=False, form="test", source={},
    )  # fmt: skip


QUERIES = (
    query("t-old", "dividend requirement 1 records every quarter", ("cb",), date(2025, 12, 31),
          f"{D1V1.doc_id}#p1"),
    query("t-new", "dividend requirement 1 records every month", ("cb",), date(2026, 1, 1),
          f"{D1V2.doc_id}#p1"),
    query("t-nbfc", "liquidity coverage each day", ("nbfc",), date(2026, 1, 1),
          f"{D2.doc_id}#p7"),
    query("t-sfb", "provisioning decisions", ("sfb",), date(2025, 6, 1), f"{D3.doc_id}#p10"),
    query("t-test", "outsourcing decisions", ("ucb",), date(2026, 6, 1), f"{D4.doc_id}#p6",
          split="test"),
)  # fmt: skip
REFERENCE = query(
    "w-1", f"Is circular {REF_SURFACE} still in force?", ("cb",), date(2026, 6, 1),
    f"{D4.doc_id}#p1", set="withdrawal",
)  # fmt: skip


@pytest.fixture(scope="module")
def scratch(writer_dsn: str, bootstrapped: None) -> Iterator[str]:
    inputs = build_inputs()
    name = make_schema_name("test", date.today(), secrets.token_hex(4))
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        e = HashEmbedder()
        todo = plan(c, {x.content_hash: x.embed_text for x in inputs.chunks}, e.identity,
                    eur_per_usd=0.9)  # fmt: skip
        embed_missing(c, e, todo.misses)
        assemble(c, name, inputs, e.identity, baseline=None)
    yield name
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        if schema_exists(c, name):
            drop(c, name)


@pytest.fixture
def vectors(tmp_path: Path) -> Path:
    refresh([q.text for q in (*QUERIES, REFERENCE)], HashEmbedder(), directory=tmp_path)
    return tmp_path


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every HTTP request and every Python socket connection fails the test. libpq opens its
    own sockets in C, so the database stays reachable and nothing else does."""
    calls: list[str] = []

    def refuse(*args: object, **kwargs: object) -> None:
        calls.append(repr(args[:2]))
        raise AssertionError("network call during an eval run")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return calls


def sets_of(*queries: EvalQuery) -> dict[str, EvalSet]:
    by: dict[str, list[EvalQuery]] = {}
    for q in queries:
        by.setdefault(q.set, []).append(q)
    return {k: EvalSet(k, tuple(v)) for k, v in by.items()}


def go(
    dsn: str, schema: str, vectors: Path, counter: Path, **kw: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    e = HashEmbedder()
    return run(
        connect=lambda: psycopg.connect(dsn, autocommit=True),
        schema=schema,
        sets=kw.pop("sets", sets_of(*QUERIES, REFERENCE)),
        split=kw.pop("split", "dev"),
        arms=kw.pop("arms", [a for a in ARMS if a.corpus == "full"]),
        embedders=lambda arm: CachedQueryEmbedder(e.identity, vectors),
        counter=counter,
        **kw,
    )


def test_a_full_eval_run_makes_zero_network_calls(
    reader_dsn: str, scratch: str, vectors: Path, tmp_path: Path, no_network: list[str]
) -> None:
    rows, summary = go(reader_dsn, scratch, vectors, tmp_path / "counter.json")
    assert no_network == []
    full_arms = {a.name for a in ARMS if a.corpus == "full"}
    assert {r["arm"] for r in rows} == full_arms
    assert len(rows) == len(full_arms) * 5  # four temporal dev queries and one withdrawal
    assert {s["bucket"] for s in summary} == {"all", "main", "holdout"}


def test_entity_and_as_of_accuracy_are_exactly_one_on_a_clean_build(
    reader_dsn: str, scratch: str, vectors: Path, tmp_path: Path
) -> None:
    _, summary = go(reader_dsn, scratch, vectors, tmp_path / "c.json")
    for s in summary:
        assert s["entity_violations"] == 0, s["arm"]
        assert s["as_of_violations"] == 0, s["arm"]
        if s["entity_returned"]:
            assert s["entity_accuracy"] == 1.0
            assert s["as_of_accuracy"] == 1.0


def test_the_version_boundary_finds_each_side_and_the_reference_path_finds_the_paragraph(
    reader_dsn: str, scratch: str, vectors: Path, tmp_path: Path
) -> None:
    rows, _ = go(reader_dsn, scratch, vectors, tmp_path / "c.json", arms=[BY_NAME["hybrid"]])
    by = {r["query_id"]: r for r in rows}
    assert by["t-old"]["returned_para_ids"][0] != by["t-new"]["returned_para_ids"][0]
    assert all(D1V2.doc_id not in p for p in by["t-old"]["returned_para_ids"])
    assert by["w-1"]["path"] == "reference"
    assert by["w-1"]["para_rank"] == 1


def test_a_missing_query_vector_stops_the_run_instead_of_embedding_live(
    reader_dsn: str, scratch: str, vectors: Path, tmp_path: Path, no_network: list[str]
) -> None:
    unseen = query("t-x", "a query nobody refreshed", ("cb",), date(2026, 1, 1),
                   f"{D1V2.doc_id}#p2")  # fmt: skip
    with pytest.raises(MissingQueryVectorError, match="a query nobody refreshed"):
        go(reader_dsn, scratch, vectors, tmp_path / "c.json", sets=sets_of(unseen))
    assert no_network == []


def test_the_test_split_counter_increments_and_persists(
    reader_dsn: str, scratch: str, vectors: Path, tmp_path: Path
) -> None:
    counter = tmp_path / "test-evaluations.json"
    _, dev = go(reader_dsn, scratch, vectors, counter, arms=[BY_NAME["hybrid"]])
    assert not counter.exists()  # dev runs are not counted
    assert {s["test_evaluations"] for s in dev} == {0}
    for expected in (1, 2):
        rows, summary = go(reader_dsn, scratch, vectors, counter, split="test",
                           arms=[BY_NAME["hybrid"]])  # fmt: skip
        assert json.loads(counter.read_text())["temporal"] == expected
        assert {s["test_evaluations"] for s in summary if s["set"] == "temporal"} == {expected}
    assert {r["query_id"] for r in rows} == {"t-test"}


def test_the_runner_defaults_to_dev_and_gates_the_shipped_arm() -> None:
    from evals.retrieval import parser

    args = parser().parse_args(["--schema", "idx_rbi_20261120_38ede3c", "--out", "r.parquet"])
    assert args.split == "dev"
    assert args.arms == "hybrid"
