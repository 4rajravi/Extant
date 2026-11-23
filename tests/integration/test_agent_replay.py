"""The graph end to end on recorded fixtures: real retrieval outputs and real model responses,
replayed with zero network calls and zero cost.

A recorded fixture must replay to exactly what it recorded. A derived fixture (one named
change to a recorded one, for an outcome the corpus cannot produce) must replay to the class
it exists for. Together they cover every refusal class the graph raises and every caveat.
"""

from __future__ import annotations

import json
import socket
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from extant.agent.graph import ask, build
from extant.agent.index import RecordedIndex
from extant.agent.llm import ReplayModel
from extant.agent.models import AgentState, Caveat, RefusalReason
from extant.agent.runtime import Deps

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "agent"
FILES = sorted(FIXTURES.glob("*.json"))

# What each derived fixture exists to produce.
DERIVED_EXPECT = {
    "derived_non_binding_support_only": ("refusal", RefusalReason.NON_BINDING_SUPPORT_ONLY, None),
    "derived_non_binding_cited": ("answer", None, Caveat.NON_BINDING_CITED),
    "derived_amendment_pending": ("answer", None, Caveat.AMENDMENT_PENDING),
    "derived_degraded_retrieval": ("answer", None, Caveat.DEGRADED_RETRIEVAL),
    "derived_verification_failed": ("refusal", RefusalReason.VERIFICATION_FAILED, None),
}

pytestmark = pytest.mark.skipif(not FILES, reason="no recorded fixtures: run extant.agent.record")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network call during a replay")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def replay(data: dict[str, Any]) -> AgentState:
    store = data["models"]
    models = {
        role: ReplayModel(model, store, source=FIXTURES)
        for role, model in (
            ("parse", "claude-haiku-5-5"),
            ("compose", "claude-opus-5-5"),
            ("judge", "claude-haiku-5-5"),
        )
    }
    deps = Deps(
        models=models,
        index=RecordedIndex(data["index"], data["embedding_model"]),
        entities=data["entities"],
    )
    spec = data["spec"]
    return ask(
        build(deps),
        spec["question"],
        index_version=data["index_version"],
        entity=spec["entity"],
        as_of=date.fromisoformat(spec["as_of"]) if spec["as_of"] else None,
        today=date.fromisoformat(data["today"]),
        request_id=spec["name"],
    )


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_a_fixture_replays_with_no_network(path: Path, no_network: None) -> None:
    data = _load(path)
    state = replay(data)
    assert state.response is not None
    if data.get("recorded"):
        r = data["recorded"]
        assert state.path == r["path"]
        assert state.outcome == r["outcome"]
        assert (state.refusal_reason.value if state.refusal_reason else None) == r["refusal_reason"]
        assert [c.value for c in state.caveats] == r["caveats"]
        assert [n.node for n in state.node_log] == [n["node"] for n in r["node_log"]]
        assert state.response.model_dump(mode="json")["result"] == r["response"]["result"]
    else:
        outcome, reason, caveat = DERIVED_EXPECT[data["spec"]["name"]]
        assert state.outcome == outcome
        assert state.refusal_reason is reason
        if caveat is not None:
            assert caveat in state.caveats


def test_the_fixtures_cover_every_refusal_class_and_every_caveat(no_network: None) -> None:
    reasons, caveats = set(), set()
    for path in FILES:
        state = replay(_load(path))
        if state.refusal_reason is not None:
            reasons.add(state.refusal_reason)
        if state.outcome == "answer":
            caveats.update(state.caveats)
    raised_by_graph = set(RefusalReason) - {RefusalReason.CAP_REACHED}
    assert reasons == raised_by_graph, sorted(raised_by_graph - reasons)
    assert caveats == set(Caveat), sorted(set(Caveat) - caveats)


def test_every_path_logs_one_row_per_node_and_the_fast_path_spends_nothing(
    no_network: None,
) -> None:
    for path in FILES:
        state = replay(_load(path))
        assert len(state.node_log) == len({n.node for n in state.node_log})
        if state.path == "fast":
            assert sum(n.calls + n.embedding_calls for n in state.node_log) == 0
            assert state.response is not None
            assert state.response.cost_eur == 0
