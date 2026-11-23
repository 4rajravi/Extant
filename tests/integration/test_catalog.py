"""The Phase 7 lookups against a real build of the synthetic corpus, plus one direction whose
second version RBI (in this fiction) published only as a PDF: the index holds v1 and v3."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from psycopg import sql

from extant import catalog as cat
from extant.core.identity import make_schema_name
from extant.core.schema import drop, schema_exists
from extant.index.assemble import assemble
from extant.index.embed import HashEmbedder, embed_missing, plan
from tests.integration.synthetic import D1V1, D1V2, D2, D3, D4, REF, REF_SURFACE, Spec
from tests.integration.synthetic import build_inputs as synthetic_inputs

pytestmark = pytest.mark.integration

D5V1 = Spec(
    "rbi:md:2025:0005:v1", "Ibex Directions, 2025", ("cb",), date(2025, 2, 1), date(2026, 3, 1)
)
D5V3 = Spec(
    "rbi:md:2025:0005:v3",
    "Ibex Directions, 2025",
    ("cb",),
    date(2026, 3, 1),
    None,
    supersedes=D5V1.doc_id,
    deleted=(9,),
)
RETIRED = "rbi:md:2025:0004:v1#p99"


@pytest.fixture(scope="module")
def schema(writer_dsn: str, bootstrapped: None) -> Iterator[str]:
    inputs = synthetic_inputs(extra=(D5V1, D5V3))
    name = make_schema_name("test", date.today(), secrets.token_hex(4))
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        e = HashEmbedder()
        todo = plan(
            c, {x.content_hash: x.embed_text for x in inputs.chunks}, e.identity, eur_per_usd=0.9
        )
        embed_missing(c, e, todo.misses)
        assemble(c, name, inputs, e.identity, baseline=None)
        c.execute(
            sql.SQL(
                "INSERT INTO {}.para_id_aliases (old_para_id, new_para_id, retired_in, reason)"
                " VALUES (%s, %s, 'idx_test_old', 'renumbered by the parser')"
            ).format(sql.Identifier(name)),
            (RETIRED, f"{D4.doc_id}#p2"),
        )
    yield name
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        if schema_exists(c, name):
            drop(c, name)


@pytest.fixture
def conn(reader_dsn: str, schema: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(reader_dsn, autocommit=True) as c:
        c.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        yield c


# --------------------------------------------------------------------------- a paragraph


def test_a_paragraph_comes_with_its_scope_validity_and_authority(conn: psycopg.Connection) -> None:
    p = cat.fetch_paragraph(conn, f"{D1V1.doc_id}#p1")
    assert p is not None
    assert (p.doc_id, p.doc_title, p.paragraph_number) == (D1V1.doc_id, D1V1.title, "1")
    assert (p.in_force_from, p.in_force_to) == (D1V1.valid_from, D1V1.valid_to)
    assert p.entity_scope == ["cb"]
    assert p.binding
    assert p.resolved_from is None


def test_a_split_paragraph_is_one_record_with_every_code(conn: psycopg.Connection) -> None:
    p = cat.fetch_paragraph(conn, f"{D2.doc_id}#p7")
    assert p is not None
    assert p.entity_scope == ["nbfc", "nbfc_hfc"]
    assert p.in_force_to is None  # open ended
    assert p.body.startswith("7. Sentence 1 ")


def test_a_retired_id_resolves_and_says_so(conn: psycopg.Connection) -> None:
    p = cat.fetch_paragraph(conn, RETIRED)
    assert p is not None
    assert p.para_id == f"{D4.doc_id}#p2"
    assert (p.resolved_from, p.resolution_reason) == (RETIRED, "renumbered by the parser")


def test_an_unknown_id_is_none_and_a_malformed_one_is_refused(conn: psycopg.Connection) -> None:
    assert cat.fetch_paragraph(conn, f"{D4.doc_id}#p404") is None
    with pytest.raises(ValueError, match="not a paragraph identifier"):
        cat.fetch_paragraph(conn, "p1; DROP TABLE paragraphs")


# --------------------------------------------------------------------------- directions in force


def test_directions_in_force_follow_the_date(conn: psycopg.Connection) -> None:
    before = cat.directions_in_force(conn, ["cb"], date(2025, 6, 1))
    after = cat.directions_in_force(conn, ["cb"], date(2026, 6, 1))
    assert [d.doc_id for d in before] == [D5V1.doc_id, D4.doc_id, D1V1.doc_id]  # by title
    assert [d.doc_id for d in after] == [D5V3.doc_id, D4.doc_id, D1V2.doc_id]
    assert all(d.topic_score is None for d in after)


def test_directions_in_force_follow_the_entity(conn: psycopg.Connection) -> None:
    assert [d.doc_id for d in cat.directions_in_force(conn, ["nbfc_hfc"], date(2026, 1, 1))] == [
        D2.doc_id
    ]
    assert cat.directions_in_force(conn, ["sfb"], date(2025, 6, 1))[0].doc_id == D3.doc_id
    assert cat.directions_in_force(conn, ["sfb"], date(2025, 9, 15)) == []  # its window closed


def test_a_topic_filters_and_ranks(conn: psycopg.Connection) -> None:
    ranked = cat.directions_in_force(conn, ["cb", "ucb"], date(2026, 6, 1), "dividend")
    assert ranked
    assert all((d.topic_score or 0) > 0 for d in ranked)
    scores = [d.topic_score or 0 for d in ranked]
    assert scores == sorted(scores, reverse=True)
    assert cat.directions_in_force(conn, ["cb"], date(2026, 6, 1), "xylophone") == []
    with pytest.raises(ValueError, match="no searchable words"):
        cat.directions_in_force(conn, ["cb"], date(2026, 6, 1), "%%")


# --------------------------------------------------------------------------- a circular


def test_a_withdrawn_circular_resolves_with_no_embedding_call(conn: psycopg.Connection) -> None:
    # _NeverEmbed raises on any call, so reaching the asserts proves there was none.
    r = cat.resolve_reference(conn, f"what replaced {REF_SURFACE}?", date(2025, 12, 1), ["ucb"])
    assert (r.canonical_ref, r.status) == (REF, "withdrawn")
    assert r.withdrawals[0].withdrawn_by == "rbi:circ:2025:0100:v1"
    assert r.consolidated_into == [D4.doc_id]
    assert f"{D4.doc_id}#p1" in [m.para_id for m in r.mentions]
    assert "consolidated into" in r.message


def test_mentions_obey_the_entity_filter_and_the_withdrawal_does_not(
    conn: psycopg.Connection,
) -> None:
    r = cat.resolve_reference(conn, REF_SURFACE, date(2025, 12, 1), ["nbfc"])
    assert r.status == "withdrawn"  # a withdrawal holds for every entity
    assert r.mentions == []  # the citing paragraph is in a cb/ucb direction


def test_a_withdrawal_after_the_date_is_not_reported_as_in_effect(
    conn: psycopg.Connection,
) -> None:
    r = cat.resolve_reference(conn, REF_SURFACE, date(2025, 6, 1), ["cb"])
    assert r.status == "withdrawn_later"
    assert "may still have applied" in r.message


def test_an_unknown_circular_is_not_found_and_no_circular_is_refused(
    conn: psycopg.Connection,
) -> None:
    r = cat.resolve_reference(conn, "DBR.No.BP.BC.99/21.04.048/2016-17", date(2026, 1, 1), ["cb"])
    assert (r.status, r.withdrawals, r.consolidated_into, r.mentions) == ("not_found", [], [], [])
    with pytest.raises(ValueError, match="no circular reference"):
        cat.resolve_reference(conn, "the dividend circular", date(2026, 1, 1), ["cb"])


# --------------------------------------------------------------------------- versions


def test_a_complete_history_has_no_gaps(conn: psycopg.Connection) -> None:
    h = cat.version_history(conn, f"{D1V2.doc_id}#p3")  # any id of the direction works
    assert h is not None
    assert [v.doc_id for v in h.versions] == [D1V1.doc_id, D1V2.doc_id]
    assert h.missing == []
    assert (h.covered_from, h.covered_to) == (D1V1.valid_from, None)


def test_a_pdf_only_version_is_named_as_missing(conn: psycopg.Connection) -> None:
    h = cat.version_history(conn, D5V3.doc_id)
    assert h is not None
    assert [v.version_seq for v in h.versions] == [1, 3]
    assert [(m.version_seq, m.after, m.before) for m in h.missing] == [
        (2, D5V1.doc_id, D5V3.doc_id)
    ]


def test_a_diff_across_a_version_boundary_lists_each_change(conn: psycopg.Connection) -> None:
    d = cat.diff_versions(conn, D1V1.doc_id, date(2025, 6, 1), date(2026, 6, 1))
    assert d is not None
    assert (d.status, d.from_version, d.to_version) == ("changed", D1V1.doc_id, D1V2.doc_id)
    assert len(d.changes) == 10
    assert d.unchanged_paragraphs == 0
    first = d.changes[0]
    assert (first.anchor, first.kind) == ("p1", "changed")
    assert first.from_body
    assert "every quarter" in first.from_body
    assert first.to_body
    assert "every month" in first.to_body


def test_one_version_on_both_dates_is_unchanged(conn: psycopg.Connection) -> None:
    d = cat.diff_versions(conn, D1V1.doc_id, date(2025, 2, 1), date(2025, 12, 31))
    assert d is not None
    assert (d.status, d.changes, d.unchanged_paragraphs) == ("unchanged", [], 10)


def test_a_date_outside_the_history_is_not_covered_never_unchanged(
    conn: psycopg.Connection,
) -> None:
    d = cat.diff_versions(conn, D1V1.doc_id, date(2024, 6, 1), date(2026, 6, 1))
    assert d is not None
    assert (d.status, d.from_version, d.changes) == ("not_covered", None, [])
    assert "none in force on 2024-06-01" in d.message


def test_a_diff_over_a_missing_version_says_so(conn: psycopg.Connection) -> None:
    d = cat.diff_versions(conn, D5V1.doc_id, date(2025, 6, 1), date(2026, 6, 1))
    assert d is not None
    assert d.status == "changed"
    # p10 became a deletion marker in v3: it is removed, not "changed" to "*****".
    assert [(c.anchor, c.kind) for c in d.changes] == [("p10", "removed")]
    assert "Version(s) 2" in d.message
    assert "only as a PDF" in d.message


def test_a_diff_refuses_reversed_dates_and_unknown_directions(conn: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="after"):
        cat.diff_versions(conn, D1V1.doc_id, date(2026, 6, 1), date(2025, 6, 1))
    assert (
        cat.diff_versions(conn, "rbi:md:2025:0999:v1", date(2025, 6, 1), date(2026, 6, 1)) is None
    )


# --------------------------------------------------------------------------- the build


def test_the_taxonomy_puts_nbfc_subtypes_under_nbfc(conn: psycopg.Connection) -> None:
    by = {e.code: e for e in cat.entity_taxonomy(conn)}
    assert by["nbfc_hfc"].parent == "nbfc"
    assert by["nbfc"].children == ["nbfc_hfc"]
    assert by["cb"].parent is None
    assert by["cb"].children == []


def test_the_manifest_names_the_build_and_its_coverage(
    conn: psycopg.Connection, schema: str
) -> None:
    m = cat.manifest(conn, schema)
    assert m["index_version"] == schema
    cov = m["coverage"]
    assert (cov["documents"], cov["directions"], cov["later_versions"]) == (7, 5, 2)
    assert cov["directions_missing_a_version"] == 1
    assert "source_sha256" not in m["build"]
