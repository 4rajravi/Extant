"""Per token prices for cost accounting, in USD per million tokens.

Anthropic's first-party price table as of 3 November 2026, and OpenAI's embedding price. A
model missing from this table is a configuration error, not a zero: an unpriced call would
make the cost column silently wrong.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input_usd_per_mtok: float
    output_usd_per_mtok: float


CHAT: dict[str, Price] = {
    "claude-haiku-5-5": Price(0.10, 0.50),
    "claude-sonnet-5-5": Price(2.00, 10.00),
    "claude-opus-5-5": Price(4.00, 20.00),
    "fake": Price(0.0, 0.0),  # tests and replays
}
EMBEDDING_USD_PER_MTOK: dict[str, float] = {
    "openai/text-embedding-3-large": 0.13,
    "test/hash-256": 0.0,
}


class UnpricedModelError(KeyError):
    pass


def chat_eur(model: str, input_tokens: int, output_tokens: int, eur_per_usd: float) -> float:
    try:
        price = CHAT[model]
    except KeyError:
        raise UnpricedModelError(f"no price for {model!r} in extant.agent.pricing") from None
    usd = (
        input_tokens * price.input_usd_per_mtok + output_tokens * price.output_usd_per_mtok
    ) / 1e6
    return usd * eur_per_usd


def embedding_eur(model_id: str, tokens: int, eur_per_usd: float) -> float:
    try:
        price = EMBEDDING_USD_PER_MTOK[model_id]
    except KeyError:
        raise UnpricedModelError(f"no price for {model_id!r} in extant.agent.pricing") from None
    return tokens * price / 1e6 * eur_per_usd
