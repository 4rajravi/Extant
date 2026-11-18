"""Settings, read from the environment or a local ``.env`` file. No key lives in code.

Every setting is an ``EXTANT_`` prefixed variable; ``.env.example`` lists all of them.
Importing this module builds ``settings`` and fails immediately, naming each missing or
invalid variable, if the environment is incomplete. Nothing has a default that points at a
real database or a real model.

Only the schema CLI and, later, the serving and build entry points import this module.
The domain models never do, so unit tests need no environment.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pydantic import AfterValidator, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from extant.core.constants import EMBEDDING_DIM, READER_ROLE
from extant.core.models import NonBlankStr, SchemaNameStr

ENV_PREFIX = "EXTANT_"


class SettingsError(RuntimeError):
    """The environment does not describe a usable configuration."""


def _dsn(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if not raw.strip():
        raise ValueError("must not be empty")
    try:
        parts = conninfo_to_dict(raw)
    except psycopg.ProgrammingError:
        raise ValueError("is not a valid PostgreSQL connection string") from None
    if not parts.get("user"):
        raise ValueError("must name its user explicitly")
    return value


Dsn = Annotated[SecretStr, AfterValidator(_dsn)]


def _embedding_dim(value: int) -> int:
    if value != EMBEDDING_DIM:
        raise ValueError(f"must be {EMBEDDING_DIM}, fixed by the halfvec({EMBEDDING_DIM}) columns")
    return value


def dsn_user(dsn: SecretStr) -> str:
    return str(conninfo_to_dict(dsn.get_secret_value())["user"])


def dsn_password(dsn: SecretStr) -> str | None:
    password = conninfo_to_dict(dsn.get_secret_value()).get("password")
    return None if password is None else str(password)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also carries docker compose's EXTANT_PG_* variables
        frozen=True,
    )

    # Database. The writer is the index plane, which creates and drops build schemas. The
    # reader is the serving plane, connected as the read only role.
    writer_dsn: Dsn
    reader_dsn: Dsn
    build_schema: SchemaNameStr  # the build serving reads, idx_<corpus>_<YYYYMMDD>_<commit>

    # Embeddings. dim and quantisation are fixed by the halfvec(256) columns and are part
    # of the vec_cache key together with the model id.
    embedding_provider: NonBlankStr
    embedding_model: NonBlankStr
    embedding_dim: Annotated[int, AfterValidator(_embedding_dim)] = EMBEDDING_DIM
    embedding_quantisation: Literal["float16"] = "float16"

    # Generation. One provider, one model tier for routing and one for composing.
    llm_provider: NonBlankStr
    router_model: NonBlankStr
    composer_model: NonBlankStr

    # Limits
    daily_answer_cap: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def _reader_is_not_writer(self) -> Self:
        if dsn_user(self.reader_dsn) == dsn_user(self.writer_dsn):
            raise ValueError(
                f"{ENV_PREFIX}READER_DSN connects as the writer's role; serving must connect"
                f" as the read only role {READER_ROLE!r}"
            )
        return self


def _describe(exc: ValidationError) -> str:
    # Deliberately never echoes input values: a DSN carries a password.
    lines = []
    for error in exc.errors(include_input=False, include_url=False):
        loc = error["loc"]
        name = f"{ENV_PREFIX}{str(loc[0]).upper()}" if loc else "settings"
        reason = "is required but not set" if error["type"] == "missing" else error["msg"]
        lines.append(f"  {name}: {reason}")
    return (
        "Extant settings are missing or invalid:\n"
        + "\n".join(lines)
        + "\nSet them in the environment or in .env (copy .env.example)."
    )


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        raise SettingsError(_describe(exc)) from None


settings: Settings = load_settings()
