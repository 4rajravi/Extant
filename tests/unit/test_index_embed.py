"""Embedding (step 6), offline: normalisation, the OpenAI client against a mock transport,
retries and the cost estimate. No request leaves the process."""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Sequence

import httpx
import pytest

from extant.core.constants import EMBEDDING_DIM
from extant.index.embed import (
    EmbeddingError,
    EmbedPlan,
    HashEmbedder,
    ModelIdentity,
    OpenAIEmbedder,
    TokenRate,
    batch_budget,
    halfvec_literal,
    l2_normalise,
)


def norm(v: Sequence[float]) -> float:
    return math.sqrt(math.fsum(x * x for x in v))


def as_float16(v: Sequence[float]) -> list[float]:
    """What halfvec stores: every value rounded to IEEE half precision."""
    return [struct.unpack("<e", struct.pack("<e", x))[0] for x in v]


def test_a_truncated_unit_vector_is_not_a_unit_vector_until_renormalised() -> None:
    full = l2_normalise([1.0 + (i % 7) for i in range(1024)])
    sliced = full[:EMBEDDING_DIM]
    assert norm(sliced) < 0.9
    assert norm(l2_normalise(sliced)) == pytest.approx(1.0, abs=1e-12)


def test_hash_vectors_are_unit_norm_at_256_dimensions_even_after_float16() -> None:
    vectors = HashEmbedder().embed([f"text {i}" for i in range(50)]).vectors
    for v in vectors:
        assert len(v) == EMBEDDING_DIM
        assert norm(v) == pytest.approx(1.0, abs=1e-12)
        assert norm(as_float16(v)) == pytest.approx(1.0, abs=1e-3)


def test_hash_vectors_are_deterministic_and_differ_by_kind() -> None:
    a = HashEmbedder().embed(["x"]).vectors[0]
    assert HashEmbedder().embed(["x"]).vectors[0] == a
    assert HashEmbedder().embed(["x"], kind="query").vectors[0] != a


def test_zero_vectors_cannot_be_normalised() -> None:
    with pytest.raises(EmbeddingError):
        l2_normalise([0.0] * 4)


def test_halfvec_literal_is_the_bracketed_list_postgres_parses() -> None:
    assert halfvec_literal([0.5, -0.25, 1e-9]) == "[0.5,-0.25,1e-09]"


# --------------------------------------------------------------------------- OpenAI


def openai(handler: httpx.MockTransport, sleeps: list[float] | None = None) -> OpenAIEmbedder:
    return OpenAIEmbedder(
        "sk-test",
        "text-embedding-3-large",
        client=httpx.Client(transport=handler),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


def ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    # Vector i is (i + 1, 1, 1, ...), so its first value over its second names it.
    data = [
        {"index": i, "embedding": [float(i + 1)] + [1.0] * (EMBEDDING_DIM - 1)}
        for i in reversed(range(len(body["input"])))
    ]
    return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 7}})


def test_openai_requests_256_dimensions_server_side_and_normalises_what_returns() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer sk-test"
        return ok(request)

    result = openai(httpx.MockTransport(handler)).embed(["a", "b"])
    assert seen == [
        {
            "model": "text-embedding-3-large",
            "input": ["a", "b"],
            "dimensions": 256,
            "encoding_format": "float",
        }
    ]
    assert len(result.vectors) == 2
    assert result.tokens == 7
    for v in result.vectors:
        assert len(v) == EMBEDDING_DIM
        assert norm(v) == pytest.approx(1.0, abs=1e-12)


def test_openai_puts_vectors_back_in_input_order() -> None:
    result = openai(httpx.MockTransport(ok)).embed(["a", "b", "c"])  # ok() answers reversed
    assert [round(v[0] / v[1], 9) for v in result.vectors] == [1.0, 2.0, 3.0]


def test_openai_retries_rate_limits_and_server_errors_with_backoff() -> None:
    responses = iter([httpx.Response(429, headers={"retry-after": "3"}), httpx.Response(502)])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses, None) or ok(request)

    sleeps: list[float] = []
    embedder = openai(httpx.MockTransport(handler), sleeps)
    assert len(embedder.embed(["a"]).vectors) == 1
    assert embedder.calls == 3
    assert sleeps == [3.0, 6.0]


def test_openai_does_not_retry_a_client_error() -> None:
    embedder = openai(httpx.MockTransport(lambda r: httpx.Response(400, text="bad model")))
    with pytest.raises(EmbeddingError, match="HTTP 400"):
        embedder.embed(["a"])
    assert embedder.calls == 1


def test_openai_refuses_a_response_of_the_wrong_dimension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 1536}]})

    with pytest.raises(EmbeddingError, match="1536 dimensions"):
        openai(httpx.MockTransport(handler)).embed(["a"])


def test_openai_needs_a_key() -> None:
    with pytest.raises(EmbeddingError, match="API key"):
        OpenAIEmbedder("")


def test_the_cost_estimate_is_tokens_times_the_listed_price() -> None:
    p = EmbedPlan(
        ModelIdentity("openai/text-embedding-3-large"), 10, {"h": "t"}, 2_000_000, eur_per_usd=0.9
    )
    assert p.usd == pytest.approx(0.26)
    assert p.eur == pytest.approx(0.234)
    assert "0.23 EUR" in str(p)


def test_openai_does_not_retry_a_request_larger_than_the_minute_budget() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "Request too large for text-embedding-3-large on tokens"
                            " per min (TPM): Limit 40000, Requested 64021."}},
        )  # fmt: skip

    embedder = openai(httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="Request too large"):
        embedder.embed(["a"])
    assert embedder.calls == 1


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_the_token_rate_never_lets_a_minute_exceed_the_limit() -> None:
    clock = Clock()
    rate = TokenRate(40_000, clock=clock, sleep=clock.sleep)
    sent: list[tuple[float, float]] = []
    for _ in range(40):
        entry = rate.reserve(8_000)  # 9,600 with the margin
        sent.append((clock.now, entry[1]))
        TokenRate.settle(entry, 8_400)  # the provider counted a little more than estimated
        clock.now += 1.0
    for start, _ in sent:
        window = sum(t for when, t in sent if start <= when < start + 60)
        assert window <= 40_000
    assert rate.waited > 0
    # 40 requests of about 8,400 tokens at 40,000 a minute take about eight minutes.
    assert 7 * 60 < clock.now - 1000 < 10 * 60


def test_a_request_that_cannot_fit_an_empty_minute_is_refused_before_sending() -> None:
    with pytest.raises(EmbeddingError, match="cannot fit"):
        TokenRate(40_000).reserve(40_000)


def test_batches_are_a_quarter_of_a_low_minute_budget() -> None:
    assert batch_budget(None) == 100_000
    assert batch_budget(40_000) == 8_333
    assert batch_budget(10_000_000) == 100_000
