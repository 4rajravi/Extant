"""``POST /ask/stream`` and the progress events behind it.

The events must be true: each one is a call from the node wrapper as a node starts or
finishes, so they follow the graph's real path, carry what the node actually wrote, and arrive
when the node does rather than on a schedule. The last event is the same ``AskResponse``
``POST /ask`` returns."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from extant.agent.progress import NodeFinished, NodeStarted, Progress
from extant.api.app import INDEX_HEADER, create_app
from extant.api.settings import ApiSettings
from extant.guards.middleware import MemoryUsageStore
from tests import replay_support
from tests.api_support import FakeRuntime, spec
from tests.mcp_support import VERSION

NOW = datetime(2026, 11, 22, 20, 0, tzinfo=UTC)
FULL_PATH = [
    "parse",
    "retrieve",
    "expand",
    "gate",
    "authority",
    "compose",
    "verify",
    "judge",
    "finalize",
]


def client(runtime: Any = None, **settings: Any) -> TestClient:
    return TestClient(
        create_app(runtime or FakeRuntime(), settings=ApiSettings(**settings), now=lambda: NOW)
    )


def events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def stream(c: TestClient, fixture: str) -> Any:
    return c.post("/ask/stream", json=spec(fixture))


# --------------------------------------------------------------------------- the stream


def test_the_stream_follows_the_graph_node_by_node_and_ends_with_the_response() -> None:
    r = stream(client(FakeRuntime("full_answer_third_person")), "full_answer_third_person")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers[INDEX_HEADER] == VERSION
    got = events(r.text)
    names = [name for name, _ in got]
    assert names[-1] == "response"
    progress = got[:-1]
    # Every node announces itself, then reports, before the next one starts.
    for (started, a), (finished, b) in zip(progress[::2], progress[1::2], strict=True):
        assert (started, finished) == ("node_started", "node_finished")
        assert a["node"] == b["node"]
    assert [data["node"] for _, data in progress[::2]] == FULL_PATH


def test_the_final_event_is_exactly_what_post_ask_returns() -> None:
    c = client(FakeRuntime("adv_twin_entity"))
    streamed = events(stream(c, "adv_twin_entity").text)[-1][1]
    plain = c.post("/ask", json=spec("adv_twin_entity")).json()
    assert streamed["retry_after"] is None
    for body in (streamed["response"], plain):
        body.pop("latency_ms")  # the one field two runs cannot share
    assert streamed["response"] == plain
    assert streamed["response"]["result"]["claims"][0]["citations"][0]["source_page"] is None


def test_the_gate_event_carries_the_rows_it_dropped() -> None:
    got = events(stream(client(FakeRuntime("adv_twin_entity")), "adv_twin_entity").text)
    finished = {d["node"]: d for name, d in got if name == "node_finished"}
    final = got[-1][1]["response"]
    reported = [row for d in finished.values() for row in d.get("dropped", [])]
    assert reported == final["dropped"]  # the same rows, in the same order, as they happened
    assert finished["retrieve"]["candidates"] > 0
    assert "checks" in finished["verify"]


def test_a_refusal_streams_as_an_outcome_with_its_reason_event() -> None:
    fixture = "entity_required_no_entity"
    got = events(stream(client(FakeRuntime(fixture)), fixture).text)
    final = got[-1][1]["response"]
    assert final["result"]["outcome"] == "refusal"
    deciding = [d for name, d in got if name == "node_finished" and "refusal_reason" in d]
    assert [d["refusal_reason"] for d in deciding] == [final["result"]["reason"]]


def test_cap_reached_streams_no_node_and_carries_retry_after() -> None:
    runtime = FakeRuntime(store=MemoryUsageStore(cap=0))
    got = events(stream(client(runtime), "full_answer_third_person").text)
    assert [name for name, _ in got] == ["response"]  # the graph never ran
    final = got[0][1]
    assert final["response"]["result"]["reason"] == "cap_reached"
    assert final["retry_after"] == "Mon, 23 Nov 2026 18:30:00 GMT"  # midnight in India


def test_the_stream_is_rate_limited_like_ask() -> None:
    c = client(ask_per_minute=1)
    assert stream(c, "full_answer_third_person").status_code == 200
    r = stream(c, "full_answer_third_person")
    assert r.status_code == 429
    assert r.json()["retry_after_seconds"] > 0


def test_a_failure_after_the_stream_began_is_a_failure_event() -> None:
    class Broken(FakeRuntime):
        def ask(self, *args: Any, **kw: Any) -> Any:
            raise RuntimeError("a bug")

    got = events(stream(client(Broken()), "full_answer_third_person").text)
    assert got == [
        (
            "failure",
            {"detail": "The request failed; nothing was answered.", "index_version": VERSION},
        )
    ]


# --------------------------------------------------------------------------- the events are true


def test_every_node_that_ran_is_reported_with_its_own_cost_row() -> None:
    seen: list[Progress] = []
    served = replay_support.serve(replay_support.load("adv_twin_entity"), observe=seen.append)
    assert served.state is not None
    finished = [e for e in seen if isinstance(e, NodeFinished)]
    assert [(e.node, e.latency_ms, e.model_calls) for e in finished] == [
        (c.node, c.latency_ms, c.calls) for c in served.state.node_log
    ]


def test_a_slow_node_holds_the_stream_on_that_node_for_as_long_as_it_takes() -> None:
    stamped: list[tuple[float, Progress]] = []
    replay_support.serve(
        replay_support.load("full_answer_third_person"),
        observe=lambda e: stamped.append((time.perf_counter(), e)),
        slow={"compose": 0.4},
    )
    started = next(t for t, e in stamped if isinstance(e, NodeStarted) and e.node == "compose")
    done = next(t for t, e in stamped if isinstance(e, NodeFinished) and e.node == "compose")
    assert done - started >= 0.4
    # Nothing else is emitted while compose runs: no event on a timer.
    assert not [e for t, e in stamped if started < t < done]


def test_without_an_observer_nothing_changes() -> None:
    data = replay_support.load("adv_twin_entity")
    quiet = replay_support.serve(data).response
    watched = replay_support.serve(data, observe=lambda e: None).response
    assert quiet.model_dump(exclude={"latency_ms"}) == watched.model_dump(exclude={"latency_ms"})


# --------------------------------------------------------------------------- the page's reads


def test_entity_types_come_from_the_taxonomy_in_its_order() -> None:
    r = client(FakeRuntime()).get("/entity_types")
    assert r.status_code == 200
    codes = [t["code"] for t in r.json()["entity_types"]]
    assert codes == sorted(codes)
    assert "cb" in codes
    assert r.json()["index_version"] == VERSION


def test_entity_types_without_the_database_is_503() -> None:
    import psycopg

    class Down(FakeRuntime):
        def entity_types(self) -> Any:
            raise psycopg.OperationalError("gone")

    r = client(Down()).get("/entity_types")
    assert (r.status_code, r.json()["index_version"]) == (503, VERSION)


def test_the_page_is_served_with_a_self_only_policy() -> None:
    c = client()
    page = c.get("/")
    assert page.status_code == 200
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert '<script src="/static/app.js" defer></script>' in page.text
    for asset in ("/static/app.js", "/static/app.css"):
        assert c.get(asset).status_code == 200
