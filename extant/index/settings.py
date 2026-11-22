"""Settings for the index build (steps 6 to 8). Read from the environment or ``.env``.

Separate from ``extant.core.settings`` so a build does not need the serving and generation
settings, and so importing it has no side effect. No key has a default.
"""

from __future__ import annotations

from typing import Annotated, Literal

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pydantic import AfterValidator, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from extant.core.constants import EMBEDDING_DIM
from extant.index.embed import Embedder, HashEmbedder, ModelIdentity, OpenAIEmbedder


class IndexSettingsError(RuntimeError):
    pass


def _dsn(value: SecretStr) -> SecretStr:
    try:
        parts = conninfo_to_dict(value.get_secret_value())
    except psycopg.ProgrammingError:
        raise ValueError("is not a valid PostgreSQL connection string") from None
    if not parts.get("user"):
        raise ValueError("must name its user explicitly")
    return value


def _dim(value: int) -> int:
    if value != EMBEDDING_DIM:
        raise ValueError(f"must be {EMBEDDING_DIM}, fixed by the halfvec({EMBEDDING_DIM}) columns")
    return value


class IndexSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXTANT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    writer_dsn: Annotated[SecretStr, AfterValidator(_dsn)]
    embedding_provider: Literal["openai", "test"]
    embedding_model: str = Field(min_length=1)
    embedding_dim: Annotated[int, AfterValidator(_dim)] = EMBEDDING_DIM
    embedding_quantisation: Literal["float16"] = "float16"
    openai_api_key: SecretStr | None = None
    # The provider account's embedding limit, tokens per minute. 40,000 is OpenAI's free usage
    # tier for text-embedding-3-large; a paid tier allows 1,000,000 or more.
    embed_tokens_per_minute: int = Field(default=40_000, gt=0)
    # EUR per USD for the cost estimate. ECB reference rate of 11 November 2026: 1 EUR is
    # 1.1127 USD, so about 0.90.
    eur_per_usd: float = Field(default=0.90, gt=0)

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(
            f"{self.embedding_provider}/{self.embedding_model}",
            self.embedding_dim,
            self.embedding_quantisation,
        )

    def embedder(self) -> Embedder:
        """The configured provider. Needs the API key only here, when vectors are wanted."""
        if self.embedding_provider == "test":
            return HashEmbedder()
        key = self.openai_api_key.get_secret_value() if self.openai_api_key else ""
        if not key:
            raise IndexSettingsError("EXTANT_OPENAI_API_KEY is not set; add it to .env")
        return OpenAIEmbedder(key, self.embedding_model, dim=self.embedding_dim)


def load_index_settings(**overrides: object) -> IndexSettings:
    try:
        return IndexSettings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        lines = [
            f"  EXTANT_{str(e['loc'][0]).upper()}: "
            + ("is required but not set" if e["type"] == "missing" else e["msg"])
            for e in exc.errors(include_input=False, include_url=False)
        ]
        raise IndexSettingsError(
            "index settings are missing or invalid:\n" + "\n".join(lines)
        ) from None
