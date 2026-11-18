"""Test configuration.

Unit tests need nothing. Tests marked ``integration`` need a real Postgres with pgvector,
reached through ``EXTANT_WRITER_DSN`` (and ``EXTANT_READER_DSN`` for the grant tests),
read from the environment or from the repository's ``.env``. Without a writer DSN a plain
``pytest`` skips them. CI sets ``EXTANT_REQUIRE_INTEGRATION=1``, which turns a missing DSN
into a hard failure so integration tests can never be skipped there unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestDatabase(BaseSettings):
    __test__ = False  # not a test class, despite the name

    model_config = SettingsConfigDict(
        env_prefix="EXTANT_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    writer_dsn: str = ""
    reader_dsn: str = ""
    require_integration: bool = False


DATABASE = TestDatabase()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if DATABASE.writer_dsn:
        return
    if DATABASE.require_integration:
        raise pytest.UsageError(
            "EXTANT_REQUIRE_INTEGRATION is set but EXTANT_WRITER_DSN is not, so the"
            " integration tests would be skipped"
        )
    skip = pytest.mark.skip(reason="integration test: set EXTANT_WRITER_DSN to run it")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
