"""The three model calls, behind one small interface.

Only ``parse``, ``compose`` and ``judge`` call a model. Each gets a ``StructuredModel``: given
a system prompt, a user message and a Pydantic schema, it returns the parsed object and the
token counts the provider reported. The real one is built with LangChain's
``init_chat_model`` from configuration (provider, model and tier are never hardcoded in a
node); tests use ``FakeModel`` and the recorded fixtures use ``RecordingModel`` and
``ReplayModel``.

Structured output uses the provider's native JSON schema mode (``method="json_schema"``),
not a forced tool call: the current Claude models reject a forced ``tool_choice``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, SecretStr

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Completion:
    parsed: BaseModel
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class StructuredModel(Protocol):
    @property
    def model(self) -> str: ...

    def complete(self, schema: type[T], *, system: str, user: str) -> Completion: ...


class ModelOutputError(RuntimeError):
    """The provider answered, but not with an object of the requested schema."""


@dataclass(frozen=True)
class Tier:
    """One model configuration: which model, and how hard it thinks."""

    provider: str
    model: str
    max_tokens: int
    effort: str | None = None  # output_config.effort; None leaves the model's default
    thinking: Mapping[str, Any] | None = None


class LangChainModel:
    """A model built by ``init_chat_model`` from a ``Tier``."""

    def __init__(self, tier: Tier, api_key: SecretStr) -> None:
        from langchain.chat_models import init_chat_model

        # No client retries: the middleware owns the one retry policy (extant.guards.middleware).
        kwargs: dict[str, Any] = {"max_tokens": tier.max_tokens, "max_retries": 0}
        if tier.effort is not None:
            kwargs["effort"] = tier.effort
        if tier.thinking is not None:
            kwargs["thinking"] = dict(tier.thinking)
        self._tier = tier
        self._chat = init_chat_model(
            tier.model,
            model_provider=tier.provider,
            api_key=api_key.get_secret_value(),
            **kwargs,
        )

    @property
    def model(self) -> str:
        return self._tier.model

    def complete(self, schema: type[T], *, system: str, user: str) -> Completion:
        from langchain_core.messages import HumanMessage, SystemMessage

        runnable = self._chat.with_structured_output(schema, method="json_schema", include_raw=True)
        start = time.perf_counter()
        out = runnable.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        latency = (time.perf_counter() - start) * 1000
        raw, parsed = out["raw"], out["parsed"]
        if parsed is None:
            raise ModelOutputError(
                f"{self.model} returned no {schema.__name__}: {out.get('parsing_error')}"
            )
        usage = getattr(raw, "usage_metadata", None) or {}
        return Completion(
            parsed=parsed,
            model=self.model,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=latency,
        )


# --------------------------------------------------------------------------- tests and fixtures


@dataclass
class FakeModel:
    """Returns whatever ``respond`` builds for each call, and records the calls."""

    respond: Callable[[type[BaseModel], str, str], BaseModel]
    name: str = "fake"
    input_tokens: int = 0
    output_tokens: int = 0
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.name

    def complete(self, schema: type[T], *, system: str, user: str) -> Completion:
        self.calls.append((schema.__name__, system, user))
        parsed = self.respond(schema, system, user)
        if not isinstance(parsed, schema):
            parsed = schema.model_validate(parsed)
        return Completion(parsed, self.name, self.input_tokens, self.output_tokens, 0.0)


def call_key(schema: type[BaseModel], system: str, user: str) -> str:
    """The identity of a model call: what was asked, of which schema."""
    blob = json.dumps([schema.__name__, system, user], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class RecordingModel:
    """Passes every call to a real model and writes the response into a fixture file."""

    def __init__(self, inner: StructuredModel, store: dict[str, Any]) -> None:
        self.inner = inner
        self.store = store

    @property
    def model(self) -> str:
        return self.inner.model

    def complete(self, schema: type[T], *, system: str, user: str) -> Completion:
        done = self.inner.complete(schema, system=system, user=user)
        self.store[call_key(schema, system, user)] = {
            "schema": schema.__name__,
            "model": done.model,
            "input_tokens": done.input_tokens,
            "output_tokens": done.output_tokens,
            "latency_ms": round(done.latency_ms, 1),
            "parsed": done.parsed.model_dump(mode="json"),
            "user": user,
        }
        return done


class FixtureMissError(LookupError):
    """A replayed graph asked a model something it was not asked when the fixture was
    recorded: the prompt or the inputs changed, and the fixture needs recording again."""


class ReplayModel:
    """Serves recorded responses, keyed by the exact call. Never touches a network."""

    def __init__(self, model: str, store: Mapping[str, Any], *, source: Path | None = None) -> None:
        self._model = model
        self.store = store
        self.source = source

    @property
    def model(self) -> str:
        return self._model

    def complete(self, schema: type[T], *, system: str, user: str) -> Completion:
        key = call_key(schema, system, user)
        entry = self.store.get(key)
        if entry is None:
            raise FixtureMissError(
                f"no recorded {schema.__name__} for this call in {self.source}; re-record"
            )
        return Completion(
            parsed=schema.model_validate(entry["parsed"]),
            model=entry["model"],
            input_tokens=int(entry["input_tokens"]),
            output_tokens=int(entry["output_tokens"]),
            latency_ms=float(entry["latency_ms"]),
        )
