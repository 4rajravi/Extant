"""The adversarial suite, the Phase 5 gate: every case asserts its specific stated reason or
caveat, and every refusal case has an answerable twin that must answer.

Runs on recorded fixtures through the real service with the network blocked, so it runs in
CI on every push at no cost. ``evals/adversarial-results.md`` holds the refusal messages for
reading; a test holds it current.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from evals.adversarial import CASES, RESULTS, Case, failures, render, run_case
from extant.agent.models import Caveat, RefusalReason
from extant.guards.pii import found
from tests.replay_support import FIXTURES, load, serve

MISSING = sorted({c.fixture for c in CASES} - {p.stem for p in FIXTURES.glob("*.json")})
pytestmark = pytest.mark.skipif(
    bool(MISSING), reason=f"fixtures not recorded yet: {MISSING}; run extant.agent.record"
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network call during the adversarial suite")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _id(case: Case) -> str:
    return case.fixture + (f"[{case.forced}]" if case.forced else "")


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            c,
            marks=pytest.mark.xfail(strict=True, reason=c.known_false_refusal),
            id=_id(c),
        )
        if c.known_false_refusal
        else pytest.param(c, id=_id(c))
        for c in CASES
    ],
)
def test_each_case_gives_its_stated_reason(case: Case) -> None:
    assert failures(case, run_case(case)) == []


def test_every_refusal_reason_and_every_caveat_is_exercised() -> None:
    reasons = {c.reason for c in CASES if c.reason is not None}
    assert reasons == set(RefusalReason), sorted(set(RefusalReason) - reasons)
    caveats = {cv for c in CASES for cv in c.caveats}
    assert caveats == set(Caveat), sorted(set(Caveat) - caveats)


def test_every_class_with_a_refusal_has_an_answerable_twin() -> None:
    # Without twins a system that refuses everything passes every case.
    by_class: dict[str, list[Case]] = {}
    for c in CASES:
        by_class.setdefault(c.cls, []).append(c)
    for cls, cases in by_class.items():
        if any(c.expect == "refusal" for c in cases):
            twins = [c for c in cases if c.expect == "answer" and c.twin_of]
            assert twins, f"{cls} has refusal cases and no answerable twin"
            for t in twins:
                assert t.twin_of in {c.fixture for c in CASES if c.expect == "refusal"}, t


def test_no_log_record_from_any_fixture_carries_an_identifier() -> None:
    records: list[Mapping[str, Any]] = []
    for path in sorted(FIXTURES.glob("*.json")):
        serve(load(path), log=records.append)
    assert len(records) == len(list(FIXTURES.glob("*.json")))
    pii_records = [r for r in records if r["redactions"]]
    assert pii_records, "the suite has a question with an identifier in it"
    for record in records:
        assert found(json.dumps(record, ensure_ascii=False)) == [], record["request_id"]


def test_the_committed_results_file_is_current(tmp_path: Path) -> None:
    fresh = render(run_case, tmp_path / "results.md").read_text(encoding="utf-8")
    assert RESULTS.read_text(encoding="utf-8") == fresh, (
        "evals/adversarial-results.md is stale: run python -m evals.adversarial render"
    )
