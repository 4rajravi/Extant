"""Step 7: a complete new build schema, or nothing.

``assemble`` creates the schema from the migrations, loads documents, paragraphs, facets,
entity types, chunks, concordance and withdrawals, attaches every chunk's vector from
``public.vec_cache``, records the manifest and runs the verification, all in one transaction.
Any failure, including a missing vector or a failed verification, rolls the whole build back:
nothing is ever migrated in place and no half-built schema is left behind.

Determinism. Steps 1 to 5 are fully deterministic: identical inputs and code versions give
byte-identical chunk rows, and tests assert it. From step 6 on, a build is reproducible only
in the weaker sense that its manifest records every input and version needed to rebuild it:
a hosted embedding API gives no guarantee of bit-identical vectors.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from extant.core.models import (
    ConcordanceEntry,
    Document,
    EntityType,
    Facet,
    Paragraph,
    Withdrawal,
)
from extant.core.schema import create
from extant.index.chunking import ChunkDraft
from extant.index.embed import ModelIdentity
from extant.index.verify import VerifyReport, table_counts, verify


class AssembleError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildInputs:
    documents: Sequence[Document]
    paragraphs: Sequence[Paragraph]
    facets: Sequence[Facet]
    entity_types: Sequence[EntityType]
    chunks: Sequence[ChunkDraft]
    concordance: Sequence[ConcordanceEntry]
    withdrawals: Sequence[Withdrawal]
    # Everything else the manifest must carry to reproduce the build: code versions, git
    # state, each document's source_sha256, where the baseline lives.
    manifest: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class AssembleResult:
    schema: str
    counts: dict[str, int]
    verify: VerifyReport | None
    seconds: float


# Keys extant.core.schema.create already writes from the schema name.
_RESERVED = frozenset({"schema", "corpus", "build_date", "git_commit", "migrations", "created_at"})

_CHUNK_COLUMNS = (
    "para_id", "sub_index", "doc_id", "ordinal", "body", "embed_text", "content_hash",
    "entity_codes", "valid_from", "valid_to", "authority_rank",
)  # fmt: skip


def _value(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, tuple):
        return list(v)
    return v


def _copy(
    conn: psycopg.Connection, schema: str, table: str, columns: Sequence[str], rows: Iterable[Any]
) -> int:
    n = 0
    statement = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
    )
    with conn.cursor() as cur, cur.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)
            n += 1
    return n


def _copy_models(
    conn: psycopg.Connection, schema: str, table: str, models: Sequence[BaseModel]
) -> int:
    if not models:
        return 0
    columns = list(type(models[0]).model_fields)
    rows = ([_value(getattr(m, c)) for c in columns] for m in models)
    return _copy(conn, schema, table, columns, rows)


def _load_chunks(
    conn: psycopg.Connection, schema: str, chunks: Sequence[ChunkDraft], identity: ModelIdentity
) -> int:
    conn.execute(
        "CREATE TEMP TABLE chunk_stage (para_id text, sub_index smallint, doc_id text,"
        " ordinal integer, body text, embed_text text, content_hash char(64),"
        " entity_codes text[], valid_from date, valid_to date, authority_rank smallint)"
        " ON COMMIT DROP"
    )
    rows = ([_value(getattr(c, col)) for col in _CHUNK_COLUMNS] for c in chunks)
    with (
        conn.cursor() as cur,
        cur.copy(
            sql.SQL("COPY chunk_stage ({}) FROM STDIN").format(
                sql.SQL(", ").join(map(sql.Identifier, _CHUNK_COLUMNS))
            )
        ) as copy,
    ):
        for row in rows:
            copy.write_row(row)
    columns = sql.SQL(", ").join(map(sql.Identifier, _CHUNK_COLUMNS))
    inserted = conn.execute(
        sql.SQL(
            "INSERT INTO {}.chunks ({cols}, embedding)"
            " SELECT {staged}, v.embedding FROM chunk_stage s"
            " JOIN public.vec_cache v ON v.content_hash = s.content_hash"
            "  AND v.model_id = %s AND v.dim = %s AND v.quantisation = %s"
            " ORDER BY s.doc_id, s.ordinal, s.sub_index"
        ).format(
            sql.Identifier(schema),
            cols=columns,
            staged=sql.SQL(", ").join(sql.SQL("s.") + sql.Identifier(c) for c in _CHUNK_COLUMNS),
        ),
        (identity.model_id, identity.dim, identity.quantisation),
    ).rowcount
    if inserted != len(chunks):
        missing = len(chunks) - inserted
        raise AssembleError(
            f"{missing} of {len(chunks)} chunks have no {identity.model_id} vector in"
            " public.vec_cache; run embed first"
        )
    return inserted


def assemble(
    conn: psycopg.Connection,
    schema: str,
    inputs: BuildInputs,
    identity: ModelIdentity,
    *,
    baseline: Path | None,
) -> AssembleResult:
    """Build ``schema`` completely and verify it, or roll everything back. With
    ``baseline`` None the verification is skipped (scratch builds in tests only)."""
    clash = _RESERVED & set(inputs.manifest)
    if clash:
        raise AssembleError(f"manifest keys {sorted(clash)} are written by schema create")
    started = datetime.now(UTC)
    with conn.transaction():
        create(conn, schema)
        documents: list[Document] = sorted(inputs.documents, key=_doc_order)
        _copy_models(conn, schema, "documents", documents)
        _copy_models(conn, schema, "paragraphs", inputs.paragraphs)
        _copy_models(conn, schema, "facets", inputs.facets)
        _copy_models(conn, schema, "entity_types", inputs.entity_types)
        _load_chunks(conn, schema, inputs.chunks, identity)
        _copy_models(conn, schema, "concordance", inputs.concordance)
        _copy_models(conn, schema, "withdrawals", inputs.withdrawals)
        conn.execute(sql.SQL("ANALYZE {}.chunks").format(sql.Identifier(schema)))

        counts = table_counts(conn, schema)
        manifest: dict[str, Any] = {
            **inputs.manifest,
            "embedding_model": identity.model_id,
            "embedding_dim": identity.dim,
            "quantisation": identity.quantisation,
            "counts": counts,
        }
        report = verify(conn, schema, baseline=baseline) if baseline is not None else None
        if report is not None:
            manifest["verify"] = report.to_json()
        manifest["built_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        with conn.cursor() as cur:
            cur.executemany(
                sql.SQL("INSERT INTO {}.build_manifest (key, value) VALUES (%s, %s)").format(
                    sql.Identifier(schema)
                ),
                [(k, Jsonb(v)) for k, v in manifest.items()],
            )
    seconds = (datetime.now(UTC) - started).total_seconds()
    return AssembleResult(schema, counts, report, seconds)


def _doc_order(d: Document) -> tuple[str, int]:
    # A supersedes_doc_id points at an earlier version; earlier versions load first.
    return (d.doc_id.rsplit(":v", 1)[0], d.version_seq)
