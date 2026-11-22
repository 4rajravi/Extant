"""Step 8: counts and coverage of a build, checked on every build; a failure fails it.

A parser regression does not crash. It silently drops paragraphs, and every metric
downstream still looks plausible, because the queries that would have failed are about text
no longer in the index. So each build's counts are compared with the previous build's
manifest, or on the first build with ``evals/baseline-counts.json`` from Phase 1, and a drop
beyond the tolerance raises. A deliberate drop is handled by committing a new baseline with a
reason, never by loosening a tolerance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, LiteralString

import psycopg
from psycopg import sql

from extant.core.identity import is_schema_name, parse_schema_name

TOLERANCES: tuple[tuple[str, float], ...] = (
    ("paragraphs", 0.02),
    ("documents", 0.0),
    ("concordance", 0.05),
    ("chunks", 0.02),
)
UNIT_NORM_TOLERANCE = 0.01  # float16 storage moves a unit vector's norm by about 1e-3


class BuildRegression(RuntimeError):  # noqa: N818 - the name the spec uses
    def __init__(self, table: str, previous: int, new: int, against: str) -> None:
        super().__init__(
            f"{table}: {new} rows, down from {previous} in {against}; a deliberate drop needs"
            " a new committed baseline with a reason"
        )
        self.table, self.previous, self.new, self.against = table, previous, new, against


class BuildIncomplete(RuntimeError):  # noqa: N818
    pass


@dataclass
class VerifyReport:
    schema: str
    counts: dict[str, int]
    compared_against: str
    previous: dict[str, int]
    checks: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "compared_against": self.compared_against,
            "previous": self.previous,
            "counts": self.counts,
            "checks": self.checks,
            "tolerances": dict(TOLERANCES),
        }


def table_counts(conn: psycopg.Connection, schema: str) -> dict[str, int]:
    s = sql.Identifier(schema)
    row = conn.execute(
        sql.SQL(
            "SELECT (SELECT count(*) FROM {s}.documents), (SELECT count(*) FROM {s}.paragraphs),"
            " (SELECT count(*) FROM {s}.chunks), (SELECT count(*) FROM {s}.concordance),"
            " (SELECT count(*) FROM {s}.withdrawals), (SELECT count(*) FROM {s}.facets),"
            " (SELECT count(*) FROM {s}.entity_types),"
            " (SELECT count(DISTINCT content_hash) FROM {s}.chunks)"
        ).format(s=s)
    ).fetchone()
    if row is None:
        raise BuildIncomplete(f"cannot count the tables of {schema}")
    keys = (
        "documents", "paragraphs", "chunks", "concordance", "withdrawals", "facets",
        "entity_types", "distinct_vectors",
    )  # fmt: skip
    return dict(zip(keys, (int(x) for x in row), strict=True))


def previous_build(conn: psycopg.Connection, schema: str) -> tuple[str, dict[str, int]] | None:
    """The latest other complete build of the same corpus, by its manifest's ``built_at``."""
    corpus = parse_schema_name(schema).corpus
    names = [
        str(r[0])
        for r in conn.execute(
            "SELECT nspname FROM pg_namespace WHERE left(nspname, 4) = 'idx_' AND nspname <> %s",
            (schema,),
        ).fetchall()
    ]
    best: tuple[str, str, dict[str, int]] | None = None
    for name in names:
        if not is_schema_name(name) or parse_schema_name(name).corpus != corpus:
            continue
        try:
            with conn.transaction():
                rows: dict[str, Any] = dict(
                    conn.execute(
                        sql.SQL(
                            "SELECT key, value FROM {}.build_manifest"
                            " WHERE key IN ('built_at', 'counts')"
                        ).format(sql.Identifier(name))
                    ).fetchall()
                )
        except psycopg.errors.UndefinedTable:
            continue
        if "built_at" not in rows or "counts" not in rows:
            continue  # an empty schema from the schema CLI, not a build
        built_at = str(rows["built_at"])
        if best is None or built_at > best[0]:
            best = (built_at, name, {k: int(v) for k, v in rows["counts"].items()})
    return (best[1], best[2]) if best else None


def baseline_counts(path: Path) -> dict[str, int]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: int(raw[k]) for k, _ in TOLERANCES if k in raw}


def verify(conn: psycopg.Connection, schema: str, *, baseline: Path) -> VerifyReport:
    """Check coverage, then counts against the previous build or the baseline. Raises
    ``BuildIncomplete`` or ``BuildRegression``; returns the report when the build passes."""
    s = sql.Identifier(schema)
    counts = table_counts(conn, schema)

    def one(query: LiteralString) -> int:
        statement = sql.SQL(query).format(s=s, tol=sql.Literal(UNIT_NORM_TOLERANCE))
        row = conn.execute(statement).fetchone()
        return int(row[0]) if row else 0

    checks = {
        "paragraphs_without_chunks": one(
            "SELECT count(*) FROM {s}.paragraphs p"
            " WHERE NOT EXISTS (SELECT 1 FROM {s}.chunks c WHERE c.para_id = p.para_id)"
        ),
        "split_paragraphs_missing_piece_0": one(
            "SELECT count(DISTINCT para_id) FROM {s}.chunks c WHERE c.sub_index > 0"
            " AND NOT EXISTS (SELECT 1 FROM {s}.chunks z"
            " WHERE z.para_id = c.para_id AND z.sub_index = 0)"
        ),
        "chunks_not_unit_norm": one(
            "SELECT count(*) FROM {s}.chunks WHERE abs(l2_norm(embedding) - 1) > {tol}"
        ),
        "chunks_without_entity_codes": one(
            "SELECT count(*) FROM {s}.chunks WHERE cardinality(entity_codes) = 0"
        ),
    }
    bad = {k: v for k, v in checks.items() if v}
    if bad:
        raise BuildIncomplete(f"{schema} fails coverage checks: {bad}")

    prior = previous_build(conn, schema)
    if prior is not None:
        against, previous = f"build {prior[0]}", prior[1]
    else:
        against, previous = f"baseline {baseline.name}", baseline_counts(baseline)
    for table, tolerance in TOLERANCES:
        if table in previous and counts[table] < previous[table] * (1 - tolerance):
            raise BuildRegression(table, previous[table], counts[table], against)
    return VerifyReport(schema, counts, against, previous, checks)


def schema_bytes(conn: psycopg.Connection, schema: str) -> int:
    """Tables, their indexes and TOAST: what the schema occupies on disk."""
    row = conn.execute(
        "SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0) FROM pg_class c"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = %s AND c.relkind IN ('r', 'm')",
        (schema,),
    ).fetchone()
    return int(row[0]) if row else 0
