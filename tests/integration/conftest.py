from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import date
from enum import Enum
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from pydantic import BaseModel

from extant.core.constants import READER_ROLE
from extant.core.identity import make_schema_name
from extant.core.schema import Connection, bootstrap, create, drop, schema_exists
from tests.conftest import DATABASE


@pytest.fixture(scope="session")
def writer_dsn() -> str:
    return DATABASE.writer_dsn


@pytest.fixture(scope="session")
def reader_dsn() -> str:
    if not DATABASE.reader_dsn:
        if DATABASE.require_integration:
            pytest.fail("EXTANT_REQUIRE_INTEGRATION is set but EXTANT_READER_DSN is not")
        pytest.skip("set EXTANT_READER_DSN to test the serving grants")
    return DATABASE.reader_dsn


def reader_password(dsn: str) -> str | None:
    parts = conninfo_to_dict(dsn) if dsn else {}
    if parts.get("user") != READER_ROLE:
        return None
    password = parts.get("password")
    return None if password is None else str(password)


@pytest.fixture
def conn(writer_dsn: str) -> Iterator[Connection]:
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        yield c


@pytest.fixture(scope="session")
def bootstrapped(writer_dsn: str) -> None:
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        bootstrap(c, reader_password=reader_password(DATABASE.reader_dsn))


def fresh_schema_name() -> str:
    return make_schema_name("test", date.today(), secrets.token_hex(4))


@pytest.fixture
def build(conn: Connection, bootstrapped: None) -> Iterator[str]:
    """A brand new index schema, dropped again after the test."""
    name = fresh_schema_name()
    create(conn, name)
    yield name
    if schema_exists(conn, name):
        drop(conn, name)


# --------------------------------------------------------------------------- rows


def _adapt(field: str, value: Any) -> Any:
    if field == "embedding":
        return "[" + ",".join(repr(float(x)) for x in value) + "]"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return list(value)
    return value


def insert(conn: Connection, schema: str, table: str, row: BaseModel) -> Any:
    """Insert a model as a row of ``schema.table`` and return the first column of the
    inserted row, which for chunks is the generated chunk_id."""
    data = {
        k: _adapt(k, v) for k, v in row.model_dump().items() if not (k == "chunk_id" and v is None)
    }
    query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({}) RETURNING *").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, data)),
        sql.SQL(", ").join(sql.Placeholder() * len(data)),
    )
    result = conn.execute(query, list(data.values())).fetchone()
    assert result is not None
    return result[0]
