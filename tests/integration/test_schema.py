"""The schema against a real Postgres with pgvector."""

from __future__ import annotations

import secrets
from datetime import date

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from extant.core.constants import EMBEDDING_DIM, READER_ROLE
from extant.core.hashing import sha256_hex
from extant.core.identity import withdrawal_key
from extant.core.models import Withdrawal
from extant.core.schema import (
    Connection,
    SchemaError,
    bootstrap,
    create,
    drop,
    list_builds,
    schema_exists,
)
from tests import factories as f
from tests.conftest import DATABASE
from tests.integration.conftest import fresh_schema_name, insert, reader_password

pytestmark = pytest.mark.integration

TABLES = {
    "documents",
    "paragraphs",
    "facets",
    "entity_types",
    "chunks",
    "concordance",
    "withdrawals",
    "para_id_aliases",
    "build_manifest",
}


def _vector_extension(conn: Connection) -> list[tuple[str, str]]:
    return [
        (str(r[0]), str(r[1]))
        for r in conn.execute(
            "SELECT n.nspname, e.extversion FROM pg_extension e"
            " JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector'"
        ).fetchall()
    ]


def _public_objects(conn: Connection) -> list[tuple[str, str]]:
    return [
        (str(r[0]), str(r[1]))
        for r in conn.execute(
            "SELECT c.relname, c.relkind FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' ORDER BY 1"
        ).fetchall()
    ]


def _seed(conn: Connection, schema: str) -> None:
    insert(conn, schema, "documents", f.document())
    insert(conn, schema, "paragraphs", f.paragraph())


# --------------------------------------------------------------------------- bootstrap


def test_bootstrap_is_idempotent(conn: Connection, bootstrapped: None) -> None:
    password = reader_password(DATABASE.reader_dsn)
    before = _public_objects(conn)
    bootstrap(conn, reader_password=password)
    bootstrap(conn, reader_password=password)
    assert _public_objects(conn) == before
    [(namespace, version)] = _vector_extension(conn)
    assert namespace == "public"
    assert tuple(int(p) for p in version.split(".")[:2]) >= (0, 7)
    assert conn.execute("SELECT to_regclass('public.vec_cache')").fetchone() == ("vec_cache",)
    roles = conn.execute("SELECT count(*) FROM pg_roles WHERE rolname = %s", (READER_ROLE,))
    assert roles.fetchone() == (1,)


def test_bootstrap_refuses_vector_installed_in_a_build_schema(
    conn: Connection, writer_dsn: str
) -> None:
    """The extension placement mistake, made on purpose in a scratch database."""
    scratch = f"extant_scratch_{secrets.token_hex(4)}"
    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
    try:
        with psycopg.connect(make_conninfo(writer_dsn, dbname=scratch), autocommit=True) as other:
            other.execute("CREATE SCHEMA idx_test_20261119_deadbee0")
            other.execute("SET search_path TO idx_test_20261119_deadbee0")
            other.execute("CREATE EXTENSION vector")
            with pytest.raises(SchemaError, match="lives in schema 'idx_test_20261119_deadbee0'"):
                bootstrap(other)
            # Nothing was created: the refusal came before the first statement.
            assert other.execute("SELECT to_regclass('public.vec_cache')").fetchone() == (None,)
            # And this is the cost the guard prevents.
            other.execute("DROP SCHEMA idx_test_20261119_deadbee0 CASCADE")
            gone = other.execute("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            assert gone.fetchone() == (0,)
    finally:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(scratch))
        )


# --------------------------------------------------------------------------- create


def test_create_produces_every_table(conn: Connection, build: str) -> None:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (build,)
    ).fetchall()
    assert {r[0] for r in rows} == TABLES


def test_chunks_embedding_is_halfvec_256(conn: Connection, build: str) -> None:
    row = conn.execute(
        "SELECT format_type(a.atttypid, a.atttypmod), tn.nspname, a.attnotnull"
        " FROM pg_attribute a"
        " JOIN pg_class c ON c.oid = a.attrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " JOIN pg_type t ON t.oid = a.atttypid"
        " JOIN pg_namespace tn ON tn.oid = t.typnamespace"
        " WHERE n.nspname = %s AND c.relname = 'chunks' AND a.attname = 'embedding'",
        (build,),
    ).fetchone()
    assert row == (f"halfvec({EMBEDDING_DIM})", "public", True)


def test_chunks_indexes_exist_and_no_ann_index(conn: Connection, build: str) -> None:
    rows = conn.execute(
        "SELECT i.relname, am.amname, t.relname FROM pg_index x"
        " JOIN pg_class i ON i.oid = x.indexrelid"
        " JOIN pg_class t ON t.oid = x.indrelid"
        " JOIN pg_am am ON am.oid = i.relam"
        " JOIN pg_namespace n ON n.oid = i.relnamespace"
        " WHERE n.nspname = %s",
        (build,),
    ).fetchall()
    chunk_indexes = {name: method for name, method, table in rows if table == "chunks"}
    assert chunk_indexes == {
        "chunks_pkey": "btree",
        "chunks_fts": "gin",
        "chunks_entity": "gin",
        "chunks_valid": "btree",
        "chunks_para": "btree",
        "chunks_para_sub": "btree",
        "chunks_doc_ord": "btree",
    }
    methods = {method for _, method, _ in rows}
    assert "hnsw" not in methods
    assert "ivfflat" not in methods


def test_create_records_the_build_manifest(conn: Connection, build: str) -> None:
    rows = dict(
        conn.execute(
            sql.SQL("SELECT key, value FROM {}.build_manifest").format(sql.Identifier(build))
        ).fetchall()
    )
    assert rows["schema"] == build
    assert rows["corpus"] == "test"
    assert rows["migrations"] == [
        "0001_initial",
        "0002_source_anchor",
        "0003_withdrawal_key",
        "0004_chunk_order",
    ]
    assert "created_at" in rows
    [info] = [b for b in list_builds(conn) if b.schema == build]
    assert info.complete
    assert (info.documents, info.paragraphs, info.chunks) == (0, 0, 0)


def test_create_refuses_an_existing_name(conn: Connection, build: str) -> None:
    with pytest.raises(SchemaError, match="already exists"):
        create(conn, build)


def test_paragraphs_are_located_by_page_or_anchor(conn: Connection, build: str) -> None:
    cols = dict(
        conn.execute(
            "SELECT table_name || '.' || column_name, is_nullable FROM information_schema.columns"
            " WHERE table_schema = %s AND column_name IN"
            " ('source_page', 'source_anchor', 'evidence_page', 'evidence_anchor')",
            (build,),
        ).fetchall()
    )
    assert cols == {
        "paragraphs.source_page": "YES",
        "paragraphs.source_anchor": "YES",
        "concordance.evidence_page": "YES",
        "concordance.evidence_anchor": "YES",
    }
    insert(conn, build, "documents", f.document())
    insert(conn, build, "paragraphs", f.paragraph(source_page=None, source_anchor="#C3K"))
    with pytest.raises(psycopg.errors.CheckViolation, match="paragraphs_located"):
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.paragraphs (para_id, doc_id, ordinal, body, content_hash)"
                " VALUES (%s, %s, %s, %s, %s)"
            ).format(sql.Identifier(build)),
            ("rbi:md:2025:0147:v1#p9", f.DOC_ID, 9, "x", sha256_hex("x")),
        )


def test_withdrawals_are_keyed_by_withdrawal_key(conn: Connection, build: str) -> None:
    pk = conn.execute(
        "SELECT a.attname FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)"
        " WHERE n.nspname = %s AND c.relname = 'withdrawals' AND i.indisprimary",
        (build,),
    ).fetchall()
    assert pk == [("withdrawal_key",)]
    # One reference, one date, two titles: two withdrawals, as RBI lists amended notifications.
    for title in (
        "Amended upto 01.10.2002 - NBFC Acceptance",
        "Amended upto 30.06.2005 - Schedules",
    ):
        key = withdrawal_key("DFC.118/DG(SPT)-98", date(1998, 1, 31), title)
        insert(
            conn,
            build,
            "withdrawals",
            Withdrawal(
                withdrawal_key=key,
                circular_ref="Notification No.DFC.118/DG(SPT)-98",
                canonical_ref="DFC.118/DG(SPT)-98",
                title=title,
                circular_date=date(1998, 1, 31),
                withdrawn_by_doc_id="rbi:circ:2025:0100:v1",
                announced_on=date(2025, 11, 28),
                effective_on=date(2025, 11, 28),
            ),
        )
    count = conn.execute(
        sql.SQL("SELECT count(*) FROM {}.withdrawals").format(sql.Identifier(build))
    ).fetchone()
    assert count == (2,)
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.withdrawals (withdrawal_key, circular_ref, withdrawn_by_doc_id,"
                " announced_on, effective_on) VALUES (%s, 'X', 'rbi:circ:2025:0100:v1',"
                " '2025-11-28', '2025-11-28')"
            ).format(sql.Identifier(build)),
            ("a" * 64,),
        )


# --------------------------------------------------------------------------- chunks


def test_fts_populates_on_insert(conn: Connection, build: str) -> None:
    _seed(conn, build)
    chunk_id = insert(conn, build, "chunks", f.chunk())
    row = conn.execute(
        sql.SQL(
            "SELECT fts::text, fts @@ to_tsquery('english', 'report & exposure'),"
            " fts @@ to_tsquery('english', 'applies')"
            " FROM {}.chunks WHERE chunk_id = %s"
        ).format(sql.Identifier(build)),
        (chunk_id,),
    ).fetchone()
    assert row is not None
    lexemes, matches_body, matches_header = row
    assert "'report'" in lexemes
    assert matches_body is True
    # fts is built from body, so the composed 'Applies to:' header is not searchable.
    assert matches_header is False


def test_entity_codes_gin_containment(conn: Connection, build: str) -> None:
    _seed(conn, build)
    ids = {
        codes: insert(
            conn,
            build,
            "chunks",
            f.chunk(entity_codes=codes, sub_index=i, embedding=f.embedding(i)),
        )
        for i, codes in enumerate([("nbfc_ul",), ("sfb", "ucb"), ("ucb",)])
    }
    query = sql.SQL(
        "SELECT chunk_id FROM {}.chunks WHERE entity_codes @> %s::text[] ORDER BY chunk_id"
    ).format(sql.Identifier(build))

    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        assert [r[0] for r in conn.execute(query, (["sfb"],))] == [ids[("sfb", "ucb")]]
        assert [r[0] for r in conn.execute(query, (["ucb"],))] == sorted(
            [ids[("sfb", "ucb")], ids[("ucb",)]]
        )
        assert [r[0] for r in conn.execute(query, (["sfb", "nbfc_ul"],))] == []
        plan = "\n".join(
            str(r[0]) for r in conn.execute(sql.SQL("EXPLAIN ") + query, (["sfb"],)).fetchall()
        )
    assert "chunks_entity" in plan


def test_embedding_round_trips_through_halfvec(conn: Connection, build: str) -> None:
    _seed(conn, build)
    chunk_id = insert(conn, build, "chunks", f.chunk(embedding=f.embedding(7)))
    row = conn.execute(
        sql.SQL(
            "SELECT vector_dims(embedding), embedding::text FROM {}.chunks WHERE chunk_id = %s"
        ).format(sql.Identifier(build)),
        (chunk_id,),
    ).fetchone()
    assert row is not None
    dims, text = row
    assert dims == EMBEDDING_DIM
    values = [float(x) for x in text.strip("[]").split(",")]
    assert values[7] == 1.0
    assert sum(values) == 1.0


def test_wrong_dimension_is_rejected_by_the_column(conn: Connection, build: str) -> None:
    _seed(conn, build)
    row = f.chunk().model_dump()
    with pytest.raises(psycopg.errors.DataException, match="expected 256 dimensions"):
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.chunks (para_id, doc_id, ordinal, body, embed_text,"
                " content_hash, entity_codes, valid_from, authority_rank, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ).format(sql.Identifier(build)),
            [
                row["para_id"],
                row["doc_id"],
                row["ordinal"],
                row["body"],
                row["embed_text"],
                row["content_hash"],
                list(row["entity_codes"]),
                row["valid_from"],
                int(row["authority_rank"]),
                "[" + ",".join(["0"] * 255) + "]",
            ],
        )


def test_pieces_of_a_paragraph_share_its_para_id_but_not_a_sub_index(
    conn: Connection, build: str
) -> None:
    _seed(conn, build)
    insert(conn, build, "chunks", f.chunk(sub_index=0))
    insert(conn, build, "chunks", f.chunk(sub_index=1, embedding=f.embedding(1)))
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert(conn, build, "chunks", f.chunk(sub_index=1, embedding=f.embedding(2)))


def test_neighbourhood_lookup_uses_the_doc_ordinal_index(conn: Connection, build: str) -> None:
    _seed(conn, build)
    insert(conn, build, "chunks", f.chunk())
    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            str(r[0])
            for r in conn.execute(
                sql.SQL(
                    "EXPLAIN SELECT para_id FROM {}.chunks WHERE doc_id = %s AND ordinal = %s"
                ).format(sql.Identifier(build)),
                (f.DOC_ID, 41),
            ).fetchall()
        )
    assert "chunks_doc_ord" in plan


# --------------------------------------------------------------------------- drop


def test_drop_leaves_public_vec_cache_and_vector_intact(
    conn: Connection, bootstrapped: None
) -> None:
    name = fresh_schema_name()
    create(conn, name)
    _seed(conn, name)
    insert(conn, name, "chunks", f.chunk())

    marker = sha256_hex(f"drop-test {name}")
    conn.execute(
        "INSERT INTO public.vec_cache (content_hash, model_id, dim, quantisation, embedding)"
        " VALUES (%s, 'drop-test', 256, 'float16', %s)",
        (marker, "[" + ",".join(["0.5"] * EMBEDDING_DIM) + "]"),
    )
    extension_before = _vector_extension(conn)
    try:
        drop(conn, name)

        assert not schema_exists(conn, name)
        # The extension placement mistake would make this the point where pgvector vanished.
        assert _vector_extension(conn) == extension_before
        assert extension_before[0][0] == "public"
        assert conn.execute("SELECT to_regtype('public.halfvec')").fetchone() == ("halfvec",)
        cached = conn.execute(
            "SELECT vector_dims(embedding) FROM public.vec_cache WHERE content_hash = %s",
            (marker,),
        ).fetchone()
        assert cached == (EMBEDDING_DIM,)
    finally:
        conn.execute("DELETE FROM public.vec_cache WHERE content_hash = %s", (marker,))


def test_create_and_drop_repeatedly_leave_public_untouched(
    conn: Connection, bootstrapped: None
) -> None:
    before = _public_objects(conn)
    extension = _vector_extension(conn)
    name = fresh_schema_name()
    for _ in range(3):
        create(conn, name)
        assert schema_exists(conn, name)
        drop(conn, name)
        assert not schema_exists(conn, name)
    assert _public_objects(conn) == before
    assert _vector_extension(conn) == extension


def test_drop_refuses_a_missing_schema(conn: Connection, bootstrapped: None) -> None:
    with pytest.raises(SchemaError, match="does not exist"):
        drop(conn, fresh_schema_name())


# --------------------------------------------------------------------------- vec_cache


def test_vec_cache_misses_when_the_model_changes(conn: Connection, build: str) -> None:
    _seed(conn, build)
    chunk = f.chunk()
    chunk_id = insert(conn, build, "chunks", chunk)
    model = f"cache-test-{build}"
    conn.execute(
        "INSERT INTO public.vec_cache (content_hash, model_id, dim, quantisation, embedding)"
        " VALUES (%s, %s, 256, 'float16', %s)",
        (chunk.content_hash, model, "[" + ",".join(["0.25"] * EMBEDDING_DIM) + "]"),
    )
    lookup = sql.SQL(
        "SELECT c.chunk_id FROM {}.chunks c"
        " LEFT JOIN public.vec_cache v"
        "   ON v.content_hash = c.content_hash"
        "  AND v.model_id = %s AND v.dim = %s AND v.quantisation = %s"
        " WHERE v.content_hash IS NULL"
    ).format(sql.Identifier(build))
    try:
        assert conn.execute(lookup, (model, 256, "float16")).fetchall() == []
        assert conn.execute(lookup, (model + "-v2", 256, "float16")).fetchall() == [(chunk_id,)]
        assert conn.execute(lookup, (model, 256, "int8")).fetchall() == [(chunk_id,)]
    finally:
        conn.execute("DELETE FROM public.vec_cache WHERE model_id = %s", (model,))


def test_vec_cache_rejects_a_malformed_hash(conn: Connection, bootstrapped: None) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO public.vec_cache (content_hash, model_id, dim, quantisation, embedding)"
            " VALUES (%s, 'bad-hash-test', 256, 'float16', %s)",
            ("A" * 64, "[" + ",".join(["0"] * EMBEDDING_DIM) + "]"),
        )


# --------------------------------------------------------------------------- serving grants


def test_reader_can_select_and_cannot_write(conn: Connection, build: str, reader_dsn: str) -> None:
    _seed(conn, build)
    insert(conn, build, "chunks", f.chunk())
    ident = sql.Identifier(build)

    with psycopg.connect(reader_dsn, autocommit=True) as reader:
        assert reader.execute("SELECT current_user").fetchone() == (READER_ROLE,)
        reader.execute(sql.SQL("SET search_path TO {}, public").format(ident))

        # The serving read path: unqualified names through search_path, halfvec resolving
        # through public.
        row = reader.execute("SELECT count(*), max(vector_dims(embedding)) FROM chunks").fetchone()
        assert row == (1, EMBEDDING_DIM)
        assert reader.execute("SELECT count(*) FROM documents").fetchone() == (1,)

        writes = [
            sql.SQL("INSERT INTO entity_types (code, label) VALUES (%s, %s)"),
            sql.SQL("UPDATE documents SET title = %s WHERE doc_id = %s"),
            sql.SQL("DELETE FROM chunks WHERE body = %s OR body = %s"),
        ]
        params = [
            ("sfb", "Small Finance Bank"),
            ("tampered", f.DOC_ID),
            ("x", "y"),
        ]
        for statement, args in zip(writes, params, strict=True):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(statement, args)

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            reader.execute("CREATE TABLE sneaky (x int)")
        # Grants cover the build schema only, not the cache in public.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            reader.execute("SELECT count(*) FROM public.vec_cache")
