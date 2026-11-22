"""The ten-query smoke suite, against a built RBI index. Marked ``smoke`` and deselected by
default: it needs ``EXTANT_BUILD_SCHEMA`` naming a build, the reader DSN, and an embedding API
key (each run embeds ten short queries, a fraction of a cent).

An unmarked query fails rather than skips: the expected paragraphs in ``evals/smoke-rbi.toml``
are a person's, never the retriever's.
"""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from extant.index.settings import load_index_settings
from extant.retrieval import retrieve
from tests.conftest import DATABASE

SUITE = Path(__file__).resolve().parents[2] / "evals" / "smoke-rbi.toml"
QUERIES: list[dict[str, Any]] = tomllib.loads(SUITE.read_text(encoding="utf-8"))["query"]
UNMARKED = "TODO_HUMAN"

pytestmark = pytest.mark.smoke


def test_the_suite_has_ten_queries_over_the_four_types() -> None:
    assert len(QUERIES) == 10
    assert {q["type"] for q in QUERIES} == {"applicability", "traceback", "diff", "as_of"}


@pytest.mark.parametrize("query", QUERIES, ids=lambda q: str(q["id"]))
def test_the_marked_paragraph_is_in_the_top_ten(query: dict[str, Any]) -> None:
    if UNMARKED in query["expected"] or not query["expected"]:
        pytest.fail(f"{query['id']} is unmarked: a person must mark its expected paragraphs")
    settings = load_index_settings()
    schema = _build_schema()
    with psycopg.connect(DATABASE.reader_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        as_of = query["as_of"]
        assert isinstance(as_of, date)
        result = retrieve(
            conn, query["text"], query["entity_codes"], as_of, embedder=settings.embedder()
        )
    ranked = [c.para_id for c in result.candidates]
    assert set(query["expected"]) & set(ranked[:10]), f"{query['id']}: got {ranked[:10]}"


def _build_schema() -> str:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Build(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="EXTANT_", env_file=".env", extra="ignore")
        build_schema: str

    return Build().build_schema  # type: ignore[call-arg]
