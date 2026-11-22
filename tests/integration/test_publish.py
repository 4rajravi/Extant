"""Publishing a build to a second database, standing in for Neon."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from extant.core.identity import make_schema_name
from extant.core.schema import drop, schema_exists
from extant.index.assemble import assemble
from extant.index.embed import HashEmbedder, embed_missing, plan
from extant.index.publish import PublishError, publish
from extant.retrieval import retrieve
from tests.integration.synthetic import build_inputs

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def built(writer_dsn: str, bootstrapped: None) -> Iterator[str]:
    inputs = build_inputs()
    name = make_schema_name("test", date.today(), secrets.token_hex(4))
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        e = HashEmbedder()
        todo = plan(c, {x.content_hash: x.embed_text for x in inputs.chunks}, e.identity,
                    eur_per_usd=0.9)  # fmt: skip
        embed_missing(c, e, todo.misses)
        assemble(c, name, inputs, e.identity, baseline=None)
    yield name
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        if schema_exists(c, name):
            drop(c, name)


@pytest.fixture(scope="module")
def other_database(writer_dsn: str) -> Iterator[str]:
    """A fresh database on the same server: a target with nothing bootstrapped in it."""
    name = f"extant_publish_{secrets.token_hex(4)}"
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield make_conninfo(writer_dsn, dbname=name)
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_a_build_publishes_whole_and_the_reader_can_retrieve_from_it(
    writer_dsn: str, reader_dsn: str, built: str, other_database: str
) -> None:
    target_reader = make_conninfo(reader_dsn, dbname=conninfo_to_dict(other_database)["dbname"])
    with (
        psycopg.connect(writer_dsn, autocommit=True) as source,
        psycopg.connect(other_database, autocommit=True) as target,
    ):
        result = publish(source, target, built, reader_dsn=reader_dsn)
        for table, n in result.counts.items():
            row = source.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(built), sql.Identifier(table)
                )
            ).fetchone()
            assert row is not None
            assert n == row[0], table
        assert result.counts["chunks"] > 0
        with pytest.raises(PublishError, match="already published"):
            publish(source, target, built, reader_dsn=reader_dsn)
    with psycopg.connect(target_reader, autocommit=True) as reader:
        reader.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(built)))
        found = retrieve(reader, "dividend records", ("cb",), date(2026, 6, 1),
                         embedder=HashEmbedder())  # fmt: skip
        assert found.candidates
        manifest = dict(reader.execute("SELECT key, value FROM build_manifest").fetchall())
        assert manifest["schema"] == built


def test_publishing_a_build_that_does_not_exist_is_refused(
    writer_dsn: str, reader_dsn: str, other_database: str
) -> None:
    with (
        psycopg.connect(writer_dsn, autocommit=True) as source,
        psycopg.connect(other_database, autocommit=True) as target,
        pytest.raises(PublishError, match="no build"),
    ):
        publish(source, target, "idx_rbi_20990101_abcdef0", reader_dsn=reader_dsn)
