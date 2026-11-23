"""The HTTP API's contract, with the FastAPI TestClient on a replaying runtime: no database,
no network. The paths that need Postgres are in tests/integration/test_api_pg.py."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from extant.agent.models import AskResponse, Refusal
from extant.agent.nodes.finalize import _NEAREST
from extant.agent.service import Readiness
from extant.api.app import INDEX_HEADER, create_app, next_cap_reset
from extant.api.ratelimit import RateLimiter, client_address
from extant.api.settings import ApiSettings
from extant.guards.middleware import MemoryUsageStore
from tests.api_support import FakeRuntime, spec, today_of
from tests.mcp_support import PARA, VERSION

NOW = datetime(2026, 11, 22, 20, 0, tzinfo=UTC)  # 01:30 on 23 November in India


def client(runtime: FakeRuntime | None = None, **settings: Any) -> TestClient:
    return TestClient(
        create_app(runtime or FakeRuntime(), settings=ApiSettings(**settings), now=lambda: NOW)
    )


def ask(c: TestClient, fixture: str, **headers: str) -> Any:
    return c.post("/ask", json=spec(fixture), headers=headers)


# --------------------------------------------------------------------------- POST /ask


def test_an_answer_is_200_with_the_phase_4_response_and_dropped_populated() -> None:
    r = ask(client(FakeRuntime("adv_twin_entity")), "adv_twin_entity")
    assert r.status_code == 200
    body = AskResponse.model_validate(r.json())
    assert body.result.outcome == "answer"
    assert len(body.dropped) == 4  # what it refused to rely on is part of the answer
    assert "Retry-After" not in r.headers


@pytest.mark.parametrize(
    "fixture", ["entity_required_no_entity", "derived_non_binding_support_only"]
)
def test_a_refusal_is_200_validates_as_refusal_and_carries_nearest_and_dropped(
    fixture: str,
) -> None:
    r = ask(client(FakeRuntime(fixture)), fixture)
    assert r.status_code == 200
    refusal = Refusal.model_validate(r.json()["result"])
    assert refusal.reason in _NEAREST
    assert refusal.nearest
    assert r.json()["dropped"]


def test_a_no_support_refusal_still_reports_what_it_dropped() -> None:
    r = ask(client(FakeRuntime("full_no_support")), "full_no_support")
    assert (r.status_code, r.json()["result"]["reason"]) == (200, "no_support_found")
    assert len(r.json()["dropped"]) == 4


def test_the_openapi_schema_documents_the_outcomes_and_the_rejection_apart() -> None:
    schema = client().get("/openapi.json").json()
    responses = schema["paths"]["/ask"]["post"]["responses"]
    assert responses["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AskResponse"
    )
    assert "Retry-After" in responses["200"]["headers"]
    assert "refusal" in responses["200"]["description"]
    assert responses["429"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorBody")
    result = schema["components"]["schemas"]["AskResponse"]["properties"]["result"]
    assert {"answer", "refusal", "retrieval_only", "unavailable"} <= set(
        result["discriminator"]["mapping"]
    )


def test_pii_is_redacted_before_the_graph_sees_it() -> None:
    runtime = FakeRuntime("adv_pii")
    r = ask(client(runtime), "adv_pii")
    assert r.status_code == 200
    seen = runtime.seen.prompts + runtime.seen.queries
    assert seen  # the graph ran: parse saw a prompt, the index a query
    for raw in ("ABCPE1234F", "001234567890"):
        assert all(raw not in text for text in seen), raw
    assert r.json()["redactions"] == ["PAN", "ACCOUNT"]
    assert "ABCPE1234F" not in r.text


def test_the_daily_cap_is_200_cap_reached_with_retry_after_and_examples() -> None:
    store = MemoryUsageStore(cap=2)
    store.days[today_of("adv_twin_entity")] = {
        "requests": 2,
        "answered": 2,
        "model_calls": 6,
        "model_eur": 0.1,
    }
    runtime = FakeRuntime("adv_twin_entity", store=store)
    r = ask(client(runtime), "adv_twin_entity")
    assert r.status_code == 200
    result = r.json()["result"]
    assert (result["outcome"], result["reason"]) == ("refusal", "cap_reached")
    assert result["examples"]
    assert parsedate_to_datetime(r.headers["Retry-After"]) == next_cap_reset(NOW)
    assert parsedate_to_datetime(r.headers["Retry-After"]) == datetime(
        2026, 11, 23, 18, 30, tzinfo=UTC
    )
    assert runtime.seen.prompts == []  # no model call


def test_a_rate_limit_breach_is_429_with_no_answer_body() -> None:
    c = client(FakeRuntime("adv_twin_entity"), ask_per_minute=1)
    assert ask(c, "adv_twin_entity").status_code == 200
    r = ask(c, "adv_twin_entity")
    assert r.status_code == 429
    assert "result" not in r.json()
    assert r.json()["index_version"] == VERSION
    assert int(r.headers["Retry-After"]) >= 1


def test_a_validation_error_does_not_echo_the_question() -> None:
    r = client().post("/ask", json={"question": "PAN ABCPE1234F " + "x" * 2000})
    assert r.status_code == 422
    assert "ABCPE1234F" not in r.text


# --------------------------------------------------------------------------- the client address


def _request(peer: str, forwarded: str | None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_the_socket_is_the_client_when_no_proxy_is_trusted() -> None:
    assert client_address(_request("10.0.0.9", "203.0.113.7"), 0) == "10.0.0.9"


def test_forwarded_for_is_read_from_the_right_when_proxies_are_trusted() -> None:
    spoofed = "198.51.100.1, 203.0.113.7, 35.191.0.1"  # caller-supplied, client, balancer
    assert client_address(_request("35.191.0.1", spoofed), 2) == "203.0.113.7"
    assert client_address(_request("35.191.0.1", "203.0.113.7"), 1) == "203.0.113.7"
    assert client_address(_request("35.191.0.1", None), 2) == "35.191.0.1"  # never trusted


def test_the_limiter_keys_on_forwarded_for_only_when_trusted() -> None:
    a, b = {"X-Forwarded-For": "203.0.113.1"}, {"X-Forwarded-For": "203.0.113.2"}
    trusted = client(FakeRuntime("adv_twin_entity"), ask_per_minute=1, trusted_proxy_hops=1)
    assert ask(trusted, "adv_twin_entity", **a).status_code == 200
    assert ask(trusted, "adv_twin_entity", **b).status_code == 200  # another caller
    untrusted = client(FakeRuntime("adv_twin_entity"), ask_per_minute=1)
    assert ask(untrusted, "adv_twin_entity", **a).status_code == 200
    assert ask(untrusted, "adv_twin_entity", **b).status_code == 429  # one socket, one caller


def test_the_window_slides() -> None:
    t = [0.0]
    limiter = RateLimiter(2, window=60, clock=lambda: t[0])
    assert limiter.hit("x") is None
    assert limiter.hit("x") is None
    assert limiter.hit("x") == 60
    t[0] = 60.5
    assert limiter.hit("x") is None


# --------------------------------------------------------------------------- the reads


def test_a_paragraph_is_found_and_an_unknown_one_is_404() -> None:
    c = client()
    found = c.get(f"/paragraph/{PARA.para_id.replace('#', '%23')}")
    assert found.status_code == 200
    assert found.json()["resolved_through_alias"] is False
    assert found.json()["note"] is None
    assert found.headers["Cache-Control"] == "public, max-age=900"
    missing = c.get("/paragraph/rbi:md:2025:0143:v1%23p99")
    assert missing.status_code == 404
    assert "Cache-Control" not in missing.headers
    assert c.get("/paragraph/not-an-id").status_code == 422


def test_examples_are_served_with_the_build_they_came_from() -> None:
    runtime = FakeRuntime()
    r = client(runtime).get("/examples")
    assert r.status_code == 200
    body = r.json()
    assert body["examples"]
    assert body["examples_index_version"] == runtime.cache.index_version  # type: ignore[union-attr]
    assert body["stale"] is (runtime.cache.index_version != VERSION)  # type: ignore[union-attr]
    assert runtime.asked == []  # no question, no graph, no model
    assert r.headers["Cache-Control"] == "public, max-age=900"


def test_a_stale_cache_is_served_and_said_to_be_stale() -> None:
    from dataclasses import replace

    runtime = FakeRuntime()
    runtime.examples = replace(runtime.examples, index_version="idx_rbi_20251101_0000000")  # type: ignore[type-var]
    body = client(runtime).get("/examples").json()
    assert body["stale"] is True
    assert "idx_rbi_20251101_0000000" in body["message"]
    assert body["examples"]


def test_no_cache_file_serves_the_static_page() -> None:
    body = client(FakeRuntime(examples=None)).get("/examples").json()
    assert (body["examples"], body["stale"]) == ([], False)
    assert body["examples_index_version"] is None


# --------------------------------------------------------------------------- health


def test_ready_reports_the_build_the_database_and_the_cap_left() -> None:
    runtime = FakeRuntime(store=MemoryUsageStore(cap=50), answered_today=12)
    r = client(runtime).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["database"], body["manifest_ok"]) == ("ready", "reachable", True)
    assert body["daily_cap"] == {"day": "2026-11-23", "cap": 50, "answered": 12, "remaining": 38}


def test_a_failed_manifest_check_is_not_ready_and_liveness_does_not_care() -> None:
    runtime = FakeRuntime(ready=Readiness(["idx_x failed verification: ['orphans']"], []))
    c = client(runtime)
    r = c.get("/health")
    assert r.status_code == 503
    assert (r.json()["status"], r.json()["manifest_ok"]) == ("not_ready", False)
    assert c.get("/health/live").status_code == 200


# --------------------------------------------------------------------------- every response


def test_every_endpoint_names_the_build() -> None:
    c = client(FakeRuntime("adv_twin_entity"))
    responses = [
        ask(c, "adv_twin_entity"),
        c.get(f"/paragraph/{PARA.para_id.replace('#', '%23')}"),
        c.get("/paragraph/rbi:md:2025:0143:v1%23p99"),
        c.get("/examples"),
        c.get("/health"),
        c.get("/health/live"),
    ]
    for r in responses:
        assert r.json()["index_version"] == VERSION, r.url
        assert r.headers[INDEX_HEADER] == VERSION, r.url


def test_the_runtime_is_closed_on_shutdown() -> None:
    runtime = FakeRuntime()
    with client(runtime):
        pass
    assert runtime.closed
