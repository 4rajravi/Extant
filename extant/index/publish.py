"""Publish a verified build to another database: Neon, for production serving and the CI gate.

    uv run python -m extant.corpora.rbi publish --schema idx_rbi_20261120_38ede3c

The target is ``EXTANT_PUBLISH_DSN``, an owner connection that lives only in ``.env``. The
read only role on the target gets the password from ``EXTANT_PUBLISH_READER_DSN``, the same
connection string the CI gate reads with (a repository secret). Publishing:

1. bootstraps the target (the vector extension, the reader role, an empty vec_cache);
2. creates the schema there from the same migrations, under the same name;
3. streams every table across with COPY, text format so pgvector versions may differ;
4. checks every row count against the source, analyses, and grants the reader.

A failure drops the half-published schema; a build is immutable, so publishing an existing
name is refused rather than overwritten. Nothing is embedded and no vector cache is copied:
the chunks carry their vectors.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from extant.core.constants import READER_ROLE
from extant.core.schema import bootstrap, create, drop, schema_exists

# Parents before children; build_manifest last, replacing the target's own rows.
TABLES = (
    "documents",
    "paragraphs",
    "facets",
    "entity_types",
    "chunks",
    "concordance",
    "withdrawals",
    "para_id_aliases",
    "build_manifest",
)


class PublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishResult:
    schema: str
    counts: dict[str, int]


def reader_password(reader_dsn: str) -> str:
    parts = conninfo_to_dict(reader_dsn)
    if parts.get("user") != READER_ROLE or not parts.get("password"):
        raise PublishError(f"the reader DSN must connect as {READER_ROLE} with a password")
    return str(parts["password"])


def _columns(conn: psycopg.Connection, schema: str, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = %s AND table_name = %s AND is_generated = 'NEVER'"
        " ORDER BY ordinal_position",
        (schema, table),
    ).fetchall()
    return [str(r[0]) for r in rows]


def _count(conn: psycopg.Connection, schema: str, table: str) -> int:
    row = conn.execute(
        sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    ).fetchone()
    return int(row[0]) if row else 0


def publish(
    source: psycopg.Connection,
    target: psycopg.Connection,
    schema: str,
    *,
    reader_dsn: str,
) -> PublishResult:
    if not schema_exists(source, schema):
        raise PublishError(f"no build {schema!r} in the source database")
    if schema_exists(target, schema):
        raise PublishError(f"{schema!r} is already published; a build never reuses a name")
    bootstrap(target, reader_password=reader_password(reader_dsn))
    create(target, schema)
    ident = sql.Identifier(schema)
    counts: dict[str, int] = {}
    try:
        with target.transaction():
            target.execute(sql.SQL("DELETE FROM {}.build_manifest").format(ident))
            for table in TABLES:
                cols = sql.SQL(", ").join(map(sql.Identifier, _columns(source, schema, table)))
                out = sql.SQL("COPY (SELECT {} FROM {}.{}) TO STDOUT").format(
                    cols, ident, sql.Identifier(table)
                )
                into = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
                    ident, sql.Identifier(table), cols
                )
                with (
                    source.cursor().copy(out) as reader,
                    target.cursor().copy(into) as writer,
                ):
                    for block in reader:
                        writer.write(block)
                counts[table] = _count(target, schema, table)
                expected = _count(source, schema, table)
                if counts[table] != expected:
                    raise PublishError(f"{table}: {counts[table]} rows published, {expected} built")
            target.execute(
                sql.SQL(
                    "SELECT setval(pg_get_serial_sequence({}, 'chunk_id'),"
                    " greatest((SELECT max(chunk_id) FROM {}.chunks), 1))"
                ).format(sql.Literal(f"{schema}.chunks"), ident)
            )
        target.execute(sql.SQL("ANALYZE {}.chunks").format(ident))
    except BaseException:
        if schema_exists(target, schema):
            drop(target, schema)
        raise
    return PublishResult(schema, counts)
