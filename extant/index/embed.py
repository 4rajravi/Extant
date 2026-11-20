"""Step 6: a vector for every distinct ``embed_text``, cached in ``public.vec_cache``.

The cache lives outside the per-build schemas and survives every rebuild. It is keyed on
``content_hash`` (``sha256(embed_text)``) plus the full model identity: model id, dimension
and quantisation. A model change therefore misses the cache instead of silently serving
another model's vectors, and a rebuild after a parser change re-embeds only the texts that
changed. This is budget control, not an optimisation.

Vectors are L2-normalised here, after they arrive and before they are stored, whatever the
provider says about its own output: a truncated unit vector is not a unit vector, and cosine
distance over un-normalised vectors is quietly wrong. OpenAI shortens server side to the
requested dimension and returns unit vectors, so for it this is a no-op; a client-side
truncation (Gemini embedding 001 below 3072, Nomic run locally) depends on it.

Writes are atomic per batch: each batch's vectors commit together or not at all, so an
interrupted run loses at most the batch in flight and a rerun pays only for what is missing.
"""

from __future__ import annotations

import hashlib
import math
import random
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx
import psycopg

from extant.core.constants import EMBEDDING_DIM
from extant.index.chunking import estimate_tokens

Kind = Literal["document", "query"]

# USD per million input tokens, from the providers' model pages, checked 19 November 2026.
PRICE_USD_PER_MTOK: dict[str, float] = {
    "openai/text-embedding-3-large": 0.13,
    "openai/text-embedding-3-small": 0.02,
    "test/hash-256": 0.0,
}


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Everything a cached vector depends on besides its text."""

    model_id: str  # <provider>/<model>
    dim: int = EMBEDDING_DIM
    quantisation: str = "float16"


@dataclass(frozen=True, slots=True)
class Embedded:
    vectors: list[list[float]]
    tokens: int  # as the provider counted them, or the estimate where it does not say


class Embedder(Protocol):
    identity: ModelIdentity

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded: ...


class EmbeddingError(RuntimeError):
    pass


def l2_normalise(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(math.fsum(x * x for x in vector))
    if norm == 0.0 or not math.isfinite(norm):
        raise EmbeddingError("cannot normalise a zero or non-finite vector")
    return [x / norm for x in vector]


def halfvec_literal(vector: Sequence[float]) -> str:
    """The text form ``::halfvec`` parses; Postgres rounds each value to float16."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


# --------------------------------------------------------------------------- providers


class OpenAIEmbedder:
    """``POST /v1/embeddings`` with ``dimensions`` set, so OpenAI shortens server side."""

    url = "https://api.openai.com/v1/embeddings"

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-large",
        *,
        dim: int = EMBEDDING_DIM,
        client: httpx.Client | None = None,
        max_attempts: int = 6,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise EmbeddingError("an OpenAI API key is required (EXTANT_OPENAI_API_KEY)")
        self.identity = ModelIdentity(f"openai/{model}", dim)
        self._model = model
        self._key = api_key
        self._client = client or httpx.Client(timeout=60.0)
        self._attempts = max_attempts
        self._sleep = sleep
        self.calls = 0

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        del kind  # the OpenAI models take no task prefix
        if not texts:
            return Embedded([], 0)
        body = {
            "model": self._model,
            "input": list(texts),
            "dimensions": self.identity.dim,
            "encoding_format": "float",
        }
        headers = {"Authorization": f"Bearer {self._key}"}
        delay = 1.0
        for attempt in range(1, self._attempts + 1):
            self.calls += 1
            try:
                response = self._client.post(self.url, json=body, headers=headers)
            except httpx.TransportError as exc:
                if attempt == self._attempts:
                    raise EmbeddingError(f"embedding request failed: {exc}") from exc
            else:
                if response.status_code == 200:
                    return self._parse(response.json(), len(texts))
                # A request over the account's whole per-minute budget is refused outright and
                # would be refused again: not a rate limit to wait out.
                too_large = response.status_code == 429 and "Request too large" in response.text
                retryable = (response.status_code == 429 and not too_large) or (
                    response.status_code >= 500
                )
                if not retryable or attempt == self._attempts:
                    raise EmbeddingError(
                        f"embedding request failed: HTTP {response.status_code}:"
                        f" {response.text[:300]}"
                    )
                after = response.headers.get("retry-after")
                if after and after.replace(".", "", 1).isdigit():
                    delay = max(delay, float(after))
            self._sleep(min(delay, 60.0))
            delay *= 2
        raise EmbeddingError("unreachable")  # pragma: no cover

    def _parse(self, payload: dict[str, object], expected: int) -> Embedded:
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise EmbeddingError(f"expected {expected} embeddings in the response")
        ordered = sorted(data, key=lambda d: int(d["index"]))
        vectors = []
        for item in ordered:
            vector = [float(x) for x in item["embedding"]]
            if len(vector) != self.identity.dim:
                raise EmbeddingError(f"got {len(vector)} dimensions, asked for {self.identity.dim}")
            vectors.append(l2_normalise(vector))
        usage = payload.get("usage")
        tokens = int(usage.get("prompt_tokens", 0)) if isinstance(usage, dict) else 0
        return Embedded(vectors, tokens)


class HashEmbedder:
    """Deterministic unit vectors seeded from each text's sha256. Not a model: for tests and
    for rehearsing a full-size build (size, latency, plans) without spending anything. Its
    own model id keeps its vectors apart from real ones in the cache."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.identity = ModelIdentity("test/hash-256", dim)
        self.calls = 0
        self.texts = 0

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        self.calls += 1
        self.texts += len(texts)
        vectors = []
        for text in texts:
            seed = hashlib.sha256(f"{kind}\0{text}".encode()).digest()
            rng = random.Random(seed)  # noqa: S311 - test vectors, not cryptography
            vectors.append(l2_normalise([rng.gauss(0.0, 1.0) for _ in range(self.identity.dim)]))
        return Embedded(vectors, sum(estimate_tokens(t) for t in texts))


# --------------------------------------------------------------------------- the cache


def cached_hashes(conn: psycopg.Connection, identity: ModelIdentity) -> set[str]:
    rows = conn.execute(
        "SELECT content_hash FROM public.vec_cache"
        " WHERE model_id = %s AND dim = %s AND quantisation = %s",
        (identity.model_id, identity.dim, identity.quantisation),
    ).fetchall()
    return {str(r[0]) for r in rows}


def write_vectors(
    conn: psycopg.Connection, identity: ModelIdentity, items: Iterable[tuple[str, list[float]]]
) -> int:
    """Insert one batch atomically; an existing row (same text, same model) is kept."""
    rows = [
        (h, identity.model_id, identity.dim, identity.quantisation, halfvec_literal(v))
        for h, v in items
    ]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO public.vec_cache (content_hash, model_id, dim, quantisation, embedding)"
            " VALUES (%s, %s, %s, %s, %s::halfvec) ON CONFLICT DO NOTHING",
            rows,
        )
    return len(rows)


@dataclass
class EmbedPlan:
    identity: ModelIdentity
    distinct_texts: int
    misses: dict[str, str]  # content_hash -> embed_text, in a stable order
    estimated_tokens: int
    eur_per_usd: float

    @property
    def usd(self) -> float:
        return self.estimated_tokens / 1e6 * PRICE_USD_PER_MTOK.get(self.identity.model_id, 0.0)

    @property
    def eur(self) -> float:
        return self.usd * self.eur_per_usd

    def __str__(self) -> str:
        price = PRICE_USD_PER_MTOK.get(self.identity.model_id)
        cost = (
            f"about {self.eur:.2f} EUR (${self.usd:.2f} at ${price}/M tokens,"
            f" {self.eur_per_usd} EUR/USD)"
            if price is not None
            else "unknown: no price on record for this model"
        )
        return (
            f"{self.identity.model_id} @ {self.identity.dim} {self.identity.quantisation}:"
            f" {self.distinct_texts:,} distinct texts, {len(self.misses):,} not cached,"
            f" about {self.estimated_tokens:,} tokens to embed (characters / 4), cost {cost}"
        )


def plan(
    conn: psycopg.Connection,
    texts: Mapping[str, str],
    identity: ModelIdentity,
    *,
    eur_per_usd: float,
) -> EmbedPlan:
    """What an embed run would do, without calling anything: the cache misses among
    ``texts`` (content_hash -> embed_text) and their estimated token count and cost."""
    have = cached_hashes(conn, identity)
    misses = {h: t for h, t in sorted(texts.items()) if h not in have}
    tokens = sum(estimate_tokens(t) for t in misses.values())
    return EmbedPlan(identity, len(texts), misses, tokens, eur_per_usd)


@dataclass
class EmbedRun:
    embedded: int = 0
    batches: int = 0
    tokens: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)


class TokenRate:
    """At most ``per_minute`` tokens in any 60 seconds, the provider's account limit.

    Each request reserves its estimated tokens times ``margin`` before it is sent (the estimate
    is characters / 4 and the provider's tokenizer may count more), and the reservation is
    replaced by the provider's own count once it answers. A request that cannot fit even in an
    empty minute is refused here, since the provider would refuse it too.
    """

    WINDOW = 60.0

    def __init__(
        self,
        per_minute: int,
        *,
        margin: float = 1.2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.per_minute = per_minute
        self.margin = margin
        self._clock = clock
        self._sleep = sleep
        self._sent: list[list[float]] = []  # [time, tokens]
        self.waited = 0.0

    def used(self) -> float:
        now = self._clock()
        self._sent = [e for e in self._sent if e[0] > now - self.WINDOW]
        return sum(e[1] for e in self._sent)

    def reserve(self, estimated: int) -> list[float]:
        tokens = estimated * self.margin
        if tokens > self.per_minute:
            raise EmbeddingError(
                f"a request of about {estimated} tokens cannot fit a {self.per_minute} token"
                " per minute limit; send smaller batches"
            )
        while self.used() + tokens > self.per_minute:
            pause = self._sent[0][0] + self.WINDOW - self._clock() + 0.05
            self.waited += max(pause, 0.0)
            self._sleep(max(pause, 0.05))
        entry = [self._clock(), tokens]
        self._sent.append(entry)
        return entry

    @staticmethod
    def settle(entry: list[float], actual: int) -> None:
        if actual > 0:
            entry[1] = float(actual)


def batch_budget(tokens_per_minute: int | None, margin: float = 1.2) -> int:
    """Estimated tokens per request: 100,000, or a quarter of the minute's budget when the
    account limit is lower, so batches interleave instead of each waiting out a full minute."""
    if tokens_per_minute is None:
        return 100_000
    return max(1, min(100_000, int(tokens_per_minute / margin / 4)))


def embed_missing(
    conn: psycopg.Connection,
    embedder: Embedder,
    misses: Mapping[str, str],
    *,
    batch_texts: int = 256,
    tokens_per_minute: int | None = None,
    progress: Callable[[EmbedRun], None] | None = None,
    rate: TokenRate | None = None,
) -> EmbedRun:
    """Embed ``misses`` in batches and write each batch to the cache as it returns. With
    ``tokens_per_minute`` (or a ``rate``), requests are paced to stay under that limit."""
    if rate is None and tokens_per_minute is not None:
        rate = TokenRate(tokens_per_minute)
    batch_tokens = batch_budget(rate.per_minute if rate else None)
    run = EmbedRun()
    started = time.monotonic()
    batch: list[tuple[str, str]] = []
    size = 0

    def flush() -> None:
        nonlocal batch, size
        if not batch:
            return
        entry = rate.reserve(size) if rate else None
        result = embedder.embed([t for _, t in batch])
        if entry is not None:
            TokenRate.settle(entry, result.tokens)
        if len(result.vectors) != len(batch):
            raise EmbeddingError("the provider returned the wrong number of vectors")
        write_vectors(
            conn, embedder.identity, zip((h for h, _ in batch), result.vectors, strict=True)
        )
        run.embedded += len(batch)
        run.batches += 1
        run.tokens += result.tokens
        run.seconds = time.monotonic() - started
        if progress is not None:
            progress(run)
        batch, size = [], 0

    for h, text in misses.items():
        tokens = estimate_tokens(text)
        if batch and (len(batch) >= batch_texts or size + tokens > batch_tokens):
            flush()
        batch.append((h, text))
        size += tokens
    flush()
    return run
