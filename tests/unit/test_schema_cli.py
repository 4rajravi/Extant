"""The schema CLI and migration files, without a database."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from psycopg import sql

from extant.core.constants import EMBEDDING_DIM, READER_ROLE
from extant.core.identity import InvalidIdentifierError
from extant.core.migrations import index_grants, index_migrations, public_migrations
from extant.core.schema import create, drop

HOSTILE = [
    "public",
    "",
    "pg_catalog",
    'idx_rbi_20261118_a3f91c4"; DROP SCHEMA public CASCADE; --',
    "idx_rbi_20261118_a3f91c4; DROP SCHEMA public CASCADE",
]


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("EXTANT_")}
    return subprocess.run(
        [sys.executable, "-m", "extant.core.schema", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("command", ["drop", "create"])
@pytest.mark.parametrize("name", HOSTILE)
def test_cli_refuses_a_hostile_schema_name_before_connecting(
    tmp_path: Path, command: str, name: str
) -> None:
    # No database and no settings are configured: the refusal has to come first.
    result = _cli(command, "--schema", name, cwd=tmp_path)
    assert result.returncode == 2
    assert "not an index schema name" in result.stderr


@pytest.mark.parametrize("name", HOSTILE)
def test_library_refuses_a_hostile_schema_name_before_using_the_connection(name: str) -> None:
    no_connection = cast(Any, None)  # any use of it would raise AttributeError instead
    with pytest.raises(InvalidIdentifierError):
        drop(no_connection, name)
    with pytest.raises(InvalidIdentifierError):
        create(no_connection, name)


def test_cli_reports_missing_settings(tmp_path: Path) -> None:
    result = _cli("list", cwd=tmp_path)
    assert result.returncode == 2
    assert "EXTANT_WRITER_DSN: is required but not set" in result.stderr


def test_migration_files_are_found_in_order() -> None:
    assert [m.name for m in public_migrations()] == [
        "0001_bootstrap",
        "0002_usage",
        "0003_alias_hits",
    ]
    assert [m.name for m in index_migrations()] == [
        "0001_initial",
        "0002_source_anchor",
        "0003_withdrawal_key",
        "0004_chunk_order",
        "0005_trusted",
    ]
    assert index_grants().path.name == "grants.sql"


def test_migrations_render_with_quoted_identifiers() -> None:
    schema = sql.Identifier("idx_rbi_20261118_a3f91c4")
    reader = sql.Identifier(READER_ROLE)
    ddl = index_migrations()[0].render(schema=schema).as_string(None)
    assert 'CREATE SCHEMA "idx_rbi_20261118_a3f91c4";' in ddl
    assert 'SET LOCAL search_path TO "idx_rbi_20261118_a3f91c4", public;' in ddl
    assert f"halfvec({EMBEDDING_DIM})" in ddl
    assert "{" not in ddl  # every placeholder was filled
    anchor = index_migrations()[1].render(schema=schema).as_string(None)
    assert 'SET LOCAL search_path TO "idx_rbi_20261118_a3f91c4", public;' in anchor
    assert "CHECK (source_page IS NOT NULL OR source_anchor IS NOT NULL)" in anchor
    assert "{" not in anchor
    key = index_migrations()[2].render(schema=schema).as_string(None)
    assert "ADD PRIMARY KEY (withdrawal_key)" in key
    assert "'^[0-9a-f]{64}$'" in key  # doubled braces render as single braces

    grants = index_grants().render(schema=schema, reader=reader).as_string(None)
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA "idx_rbi_20261118_a3f91c4" TO "extant_reader"' in (
        grants
    )

    boot = (
        public_migrations()[0]
        .render(reader=reader, reader_name=sql.Literal(READER_ROLE))
        .as_string(None)
    )
    assert "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;" in boot
    assert "'^[0-9a-f]{64}$'" in boot  # doubled braces render as single braces
    assert f"public.halfvec({EMBEDDING_DIM})" in boot


def test_ddl_has_no_ann_index() -> None:
    ddl = index_migrations()[0].path.read_text().lower()
    statements = [line for line in ddl.splitlines() if not line.lstrip().startswith("--")]
    assert not any("hnsw" in line or "ivfflat" in line for line in statements)
