"""Settings for the RBI corpus adapter, read from the environment or ``.env``.

Kept apart from ``extant.core.settings`` so the corpus adapter stays pluggable and the
offline build steps need no database or model configuration. Every variable is
``EXTANT_RBI_`` prefixed and listed in ``.env.example``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from extant import __version__

ENV_PREFIX = "EXTANT_RBI_"
MIN_REQUEST_INTERVAL_SECONDS = 2.0

_CONTACT = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+|https?://\S+")


def _contact(value: str) -> str:
    if _CONTACT.fullmatch(value) is None:
        raise ValueError("must be an email address or an http(s) URL")
    return value


class RbiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # Sent in the User-Agent of every request, so RBI can reach whoever runs the crawl.
    crawl_contact: Annotated[str, AfterValidator(_contact)]
    # Root of the raw cache and every derived cache. Git ignored.
    data_dir: Path = Path("data/rbi")
    # Politeness floor: a setting may slow the crawler down, never speed it up.
    request_interval_seconds: float = Field(default=2.0, ge=MIN_REQUEST_INTERVAL_SECONDS)
    timeout_seconds: float = Field(default=60.0, gt=0)

    @property
    def user_agent(self) -> str:
        return (
            f"Extant/{__version__} (research crawler for public RBI regulatory text;"
            f" contact: {self.crawl_contact})"
        )


class RbiSettingsError(RuntimeError):
    pass


def load_rbi_settings() -> RbiSettings:
    try:
        return RbiSettings()
    except ValidationError as exc:
        lines = [
            f"  {ENV_PREFIX}{str(e['loc'][0]).upper() if e['loc'] else ''}: "
            + ("is required but not set" if e["type"] == "missing" else e["msg"])
            for e in exc.errors(include_input=False, include_url=False)
        ]
        raise RbiSettingsError(
            "RBI corpus settings are missing or invalid:\n" + "\n".join(lines)
        ) from None
