"""Index schema tooling, and its CLI.

    python -m extant.core.schema bootstrap
    python -m extant.core.schema create --schema idx_rbi_20261118_a3f91c4
    python -m extant.core.schema drop   --schema idx_rbi_20261118_a3f91c4
    python -m extant.core.schema list

The offline index plane writes, the stateless serving plane reads, and the build schema is
the contract between them. Each build is a brand new schema; rollout is a ``search_path``
change on a new serving revision, and rollback is the previous revision still pointing at
the previous schema. Two things outlive every build and live in ``public``: the ``vector``
extension and ``public.vec_cache``.

The library functions take an open connection and do their work in one transaction, so a
half-built schema is never visible. Schema and role names cannot be bound as query
parameters, so they are validated against the identity grammar first and then passed to
the server as ``psycopg.sql.Identifier``, never through an f-string.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb

from extant.core.constants import MIN_PGVECTOR_VERSION, READER_ROLE
from extant.core.identity import InvalidIdentifierError, is_schema_name, parse_schema_name
from extant.core.migrations import index_grants, index_migrations, public_migrations

Connection = psycopg.Connection[TupleRow]


class SchemaError(RuntimeError):
    """The database is not in the state an operation requires."""


@dataclass(frozen=True, slots=True)
class BuildInfo:
    schema: str
    created_at: datetime | None
    documents: int | None
    paragraphs: int | None
    chunks: int | None

    @property
    def complete(self) -> bool:
        return self.created_at is not None


# --------------------------------------------------------------------------- checks


def _version_tuple(version: str) -> tuple[int, ...]:
    numeric = version.split("-", 1)[0]
    return tuple(int(part) for part in numeric.split(".") if part.isdigit())


def _installed_vector(conn: Connection) -> tuple[str, str] | None:
    row = conn.execute(
        "SELECT n.nspname, e.extversion FROM pg_extension e"
        " JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector'"
    ).fetchone()
    return None if row is None else (str(row[0]), str(row[1]))


def _refuse_misplaced_vector(namespace: str) -> None:
    if namespace != "public":
        raise SchemaError(
            f"the vector extension lives in schema {namespace!r}, not public. Dropping that"
            " schema would drop halfvec. Move it with ALTER EXTENSION vector SET SCHEMA public."
        )


def check_vector_extension(conn: Connection) -> str:
    """Return the installed pgvector version, or raise unless it lives in public and is
    new enough to have halfvec."""
    installed = _installed_vector(conn)
    if installed is None:
        raise SchemaError("the vector extension is not installed; run bootstrap first")
    namespace, version = installed
    _refuse_misplaced_vector(namespace)
    if _version_tuple(version) < MIN_PGVECTOR_VERSION:
        wanted = ".".join(map(str, MIN_PGVECTOR_VERSION))
        raise SchemaError(f"pgvector {version} is too old, halfvec needs {wanted} or newer")
    return version


def _check_bootstrapped(conn: Connection) -> None:
    check_vector_extension(conn)
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER_ROLE,)).fetchone() is None:
        raise SchemaError(f"role {READER_ROLE!r} does not exist; run bootstrap first")
    if conn.execute("SELECT to_regclass('public.vec_cache')").fetchone() == (None,):
        raise SchemaError("public.vec_cache does not exist; run bootstrap first")


def schema_exists(conn: Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)).fetchone()
    return row is not None


@contextlib.contextmanager
def in_build(conn: Connection, schema: str) -> Iterator[Connection]:
    """A unit of work on ``schema``, inside one transaction with the build first on the path.

    ``SET LOCAL`` lasts for the transaction, and a transaction-mode pooler (Neon's PgBouncer)
    keeps one server connection for a transaction. A session ``SET search_path`` does not
    survive such a pooler: each autocommit statement may run on another server connection,
    and the setting is silently lost (48 of 60 statements in a test against Neon). The pooler
    also refuses ``search_path`` as a startup option, so this is the way to name the build.
    Inside an open transaction it nests as a savepoint, and the setting lasts until that
    transaction ends."""
    with conn.transaction():
        conn.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema)))
        yield conn


# --------------------------------------------------------------------------- operations


def bootstrap(conn: Connection, *, reader_password: str | None = None) -> str:
    """Create the vector extension in public, the read only role and public.vec_cache.

    Idempotent. When ``reader_password`` is given the role's password is (re)set to it, so
    the reader DSN in settings works against a fresh local database. Returns the pgvector
    version.
    """
    reader = sql.Identifier(READER_ROLE)
    with conn.transaction():
        # Checked before anything runs: CREATE EXTENSION IF NOT EXISTS ... WITH SCHEMA public
        # is silently a no-op when the extension already sits in some other schema.
        installed = _installed_vector(conn)
        if installed is not None:
            _refuse_misplaced_vector(installed[0])
        for migration in public_migrations():
            conn.execute(migration.render(reader=reader, reader_name=sql.Literal(READER_ROLE)))
        version = check_vector_extension(conn)
        if reader_password is not None:
            conn.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    reader, sql.Literal(reader_password)
                )
            )
    return version


def create(conn: Connection, schema: str) -> list[str]:
    """Build one index schema from the numbered migrations, record its manifest and grant
    the read only role. Refuses an existing name: builds are immutable. Returns the
    migrations applied."""
    parts = parse_schema_name(schema)
    ident = sql.Identifier(schema)
    with conn.transaction():
        _check_bootstrapped(conn)
        if schema_exists(conn, schema):
            raise SchemaError(f"schema {schema!r} already exists; a build never reuses a name")
        applied: list[str] = []
        for migration in index_migrations():
            conn.execute(migration.render(schema=ident))
            applied.append(migration.name)

        manifest: dict[str, Any] = {
            "schema": schema,
            "corpus": parts.corpus,
            "build_date": parts.build_date.isoformat(),
            "git_commit": parts.commit,
            "migrations": applied,
        }
        insert = sql.SQL("INSERT INTO {}.build_manifest (key, value) VALUES (%s, %s)").format(ident)
        with conn.cursor() as cur:
            cur.executemany(insert, [(key, Jsonb(value)) for key, value in manifest.items()])
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.build_manifest (key, value) VALUES ('created_at', to_jsonb(now()))"
            ).format(ident)
        )

        conn.execute(index_grants().render(schema=ident, reader=sql.Identifier(READER_ROLE)))
    return applied


def drop(conn: Connection, schema: str) -> None:
    """Drop one index schema. Refuses any name outside the index schema grammar, which is
    what stops ``public`` being dropped by a typo."""
    parse_schema_name(schema)
    with conn.transaction():
        if not schema_exists(conn, schema):
            raise SchemaError(f"schema {schema!r} does not exist")
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def list_builds(conn: Connection) -> list[BuildInfo]:
    """Index schemas present in the database, oldest name first, with row counts and the
    build timestamp from each manifest."""
    names = [
        str(row[0])
        for row in conn.execute(
            "SELECT nspname FROM pg_namespace WHERE left(nspname, 4) = 'idx_' ORDER BY nspname"
        ).fetchall()
    ]
    builds: list[BuildInfo] = []
    for name in filter(is_schema_name, names):
        ident = sql.Identifier(name)
        try:
            with conn.transaction():
                created = conn.execute(
                    sql.SQL("SELECT value FROM {}.build_manifest WHERE key = 'created_at'").format(
                        ident
                    )
                ).fetchone()
                counts = conn.execute(
                    sql.SQL(
                        "SELECT (SELECT count(*) FROM {s}.documents),"
                        " (SELECT count(*) FROM {s}.paragraphs),"
                        " (SELECT count(*) FROM {s}.chunks)"
                    ).format(s=ident)
                ).fetchone()
        except psycopg.errors.UndefinedTable:
            builds.append(BuildInfo(name, None, None, None, None))
            continue
        created_at = datetime.fromisoformat(str(created[0])) if created else None
        docs, paras, chunks = counts if counts else (None, None, None)
        builds.append(BuildInfo(name, created_at, docs, paras, chunks))
    return builds


# --------------------------------------------------------------------------- CLI


def _print_builds(builds: Sequence[BuildInfo], serving: str) -> None:
    if not builds:
        print("no index schemas")
        return

    def cell(value: int | None) -> str:
        return "?" if value is None else str(value)

    header = ("", "schema", "built at", "documents", "paragraphs", "chunks")
    rows = [
        (
            "*" if b.schema == serving else "",
            b.schema,
            b.created_at.isoformat(timespec="seconds") if b.created_at else "incomplete",
            cell(b.documents),
            cell(b.paragraphs),
            cell(b.chunks),
        )
        for b in builds
    ]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for r in [header, *rows]:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip())
    print("\n* the build named by EXTANT_BUILD_SCHEMA")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m extant.core.schema", description="Manage Extant index build schemas."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "bootstrap", help="create the vector extension, the reader role and public.vec_cache"
    )
    for name, text in (("create", "build one index schema"), ("drop", "drop one index schema")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--schema", required=True, help="idx_<corpus>_<YYYYMMDD>_<commit>")
    sub.add_parser("list", help="show index schemas with row counts and build timestamps")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # Validate the name before touching settings or the database, so a hostile or mistyped
    # name is refused even with no database configured.
    if args.command in {"create", "drop"}:
        try:
            parse_schema_name(args.schema)
        except InvalidIdentifierError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    # Imported here so that the library functions above are usable without a configured
    # environment, which is how the integration tests drive them. Importing the module is
    # what validates the environment, so its SettingsError (a RuntimeError) cannot be
    # imported first and caught by name.
    try:
        from extant.core.settings import dsn_password, dsn_user, settings
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        with psycopg.connect(settings.writer_dsn.get_secret_value(), autocommit=True) as conn:
            if args.command == "bootstrap":
                password = None
                if dsn_user(settings.reader_dsn) == READER_ROLE:
                    password = dsn_password(settings.reader_dsn)
                else:
                    print(
                        f"note: EXTANT_READER_DSN does not connect as {READER_ROLE!r}, so the"
                        " role's password was left unchanged",
                        file=sys.stderr,
                    )
                version = bootstrap(conn, reader_password=password)
                print(f"bootstrapped: vector {version} in public, role {READER_ROLE}, vec_cache")
            elif args.command == "create":
                applied = create(conn, args.schema)
                print(f"created {args.schema} ({', '.join(applied)}), granted {READER_ROLE}")
            elif args.command == "drop":
                drop(conn, args.schema)
                print(f"dropped {args.schema}")
            else:
                _print_builds(list_builds(conn), settings.build_schema)
    except (SchemaError, InvalidIdentifierError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except psycopg.OperationalError as exc:
        print(f"error: cannot connect to the database: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"error: the database refused the operation: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
