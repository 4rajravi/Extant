"""Agent configuration: which provider and models each tier uses, and the key.

Read from ``.env`` like the index settings. The key is needed only when a model is actually
called, so a replayed or fake graph runs without one.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from extant.agent.llm import LangChainModel, StructuredModel, Tier
from extant.guards.middleware import RetryPolicy, UsageStore, guard_all


class AgentSettingsError(RuntimeError):
    pass


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXTANT_", env_file=".env", extra="ignore")

    llm_provider: str = "anthropic"
    router_model: str = "claude-haiku-5-5"  # the cheap tier: parse, judge
    composer_model: str = "claude-opus-5-5"  # the stronger tier: compose
    composer_effort: str = "medium"
    anthropic_api_key: SecretStr | None = None
    eur_per_usd: float = Field(default=0.90, gt=0)

    def tiers(self) -> dict[str, Tier]:
        # The cheap tier classifies and judges: no thinking, low effort, short output.
        cheap = {"thinking": {"type": "disabled"}, "effort": "low"}
        return {
            "parse": Tier(self.llm_provider, self.router_model, max_tokens=1024, **cheap),  # type: ignore[arg-type]
            "judge": Tier(self.llm_provider, self.router_model, max_tokens=256, **cheap),  # type: ignore[arg-type]
            "compose": Tier(
                self.llm_provider, self.composer_model, max_tokens=8000, effort=self.composer_effort
            ),
        }

    def models(
        self, store: UsageStore, *, retry: RetryPolicy | None = None
    ) -> dict[str, StructuredModel]:
        """The only way to get a model: each comes wrapped in the middleware (cap, redaction,
        retry, accounting), so a call added later is guarded without anyone remembering to."""
        if self.anthropic_api_key is None:
            raise AgentSettingsError("EXTANT_ANTHROPIC_API_KEY is not set in .env")
        raw: dict[str, StructuredModel] = {
            name: LangChainModel(t, self.anthropic_api_key) for name, t in self.tiers().items()
        }
        return guard_all(raw, store, self.eur_per_usd, retry=retry)


def load_agent_settings() -> AgentSettings:
    return AgentSettings()
