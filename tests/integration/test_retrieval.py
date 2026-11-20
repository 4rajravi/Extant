"""The retrieval library against a real Postgres and an assembled scratch build.

Prefilter correctness is tested exhaustively rather than by sampling: every candidate of
every query, ranked or expanded, on either path, must carry a queried entity code and be in
force on the as-of date. The hash embedder's vectors are noise, which suits this test: the
dense arm then returns whatever survives the filter, so a leak past it would show at once.
"""

from __future__ import annotations

import itertools
import secrets
from collections.abc import Iterator, Sequence
from datetime import date

import httpx
import psycopg
import pytest
from psycopg import sql

from extant.core.identity import make_schema_name
from extant.core.schema import Connection, drop, schema_exists
from extant.index.assemble import assemble
from extant.index.embed import Embedded, HashEmbedder, Kind, embed_missing, plan
from extant.retrieval import Candidate, RetrievalOptions, RetrievalResult, retrieve
from tests.integration.synthetic import D1V1, D1V2, D2, D3, D4, OVERSIZED, REF, TOPICS, build_inputs

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def index(writer_dsn: str, bootstrapped: None) -> Iterator[str]:
    inputs = build_inputs()
    name = make_schema_name("test", date.today(), secrets.token_hex(4))
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        embedder = HashEmbedder()
        todo = plan(c, {x.content_hash: x.embed_text for x in inputs.chunks}, embedder.identity,
                    eur_per_usd=0.9)  # fmt: skip
        embed_missing(c, embedder, todo.misses)
        assemble(c, name, inputs, embedder.identity, baseline=None)
    yield name
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        if schema_exists(c, name):
            drop(c, name)


@pytest.fixture
def db(writer_dsn: str, index: str) -> Iterator[Connection]:
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        c.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(index)))
        yield c


def everything(result: RetrievalResult) -> list[Candidate]:
    return [*result.candidates, *result.context]


ENTITY_SETS: tuple[tuple[str, ...], ...] = (
    ("cb",),
    ("nbfc",),
    ("nbfc_hfc",),
    ("sfb",),
    ("ucb",),
    ("cb", "sfb"),
    ("nbfc", "ucb"),
)
# Exactly on D3's valid_from and valid_to, and on the D1 v1 / v2 boundary, and either side.
AS_OF = (
    date(2024, 6, 1),
    date(2025, 3, 14),
    date(2025, 3, 15),
    date(2025, 9, 14),
    date(2025, 9, 15),
    date(2025, 12, 31),
    date(2026, 1, 1),
    date(2026, 6, 1),
)
QUERIES = ("dividend records", "capital adequacy reporting", "liquidity coverage", "outsourcing")
WIDE = RetrievalOptions(k=100, top_n=50)


def in_force(c: Candidate, as_of: date) -> bool:
    return c.valid_from <= as_of and (c.valid_to is None or c.valid_to > as_of)


@pytest.mark.parametrize("codes", ENTITY_SETS, ids="+".join)
def test_every_candidate_matches_the_entity_scope_and_is_in_force(
    db: Connection, codes: tuple[str, ...]
) -> None:
    embedder = HashEmbedder()
    for as_of, query in itertools.product(AS_OF, QUERIES):
        result = retrieve(db, query, codes, as_of, embedder=embedder, options=WIDE)
        for c in everything(result):
            assert set(c.entity_codes) & set(codes), (query, codes, as_of, c.para_id)
            assert in_force(c, as_of), (query, codes, as_of, c.para_id, c.valid_from, c.valid_to)


def docs(result: RetrievalResult) -> set[str]:
    return {c.doc_id for c in everything(result)}


def test_an_nbfc_query_never_returns_a_commercial_bank_paragraph(db: Connection) -> None:
    for query in (*QUERIES, *TOPICS):
        found = docs(retrieve(db, query, ("nbfc",), date(2026, 6, 1), embedder=HashEmbedder(),
                              options=WIDE))  # fmt: skip
        assert found <= {D2.doc_id}
        assert D1V2.doc_id not in found
        assert D4.doc_id not in found


@pytest.mark.parametrize(
    ("as_of", "present", "absent"),
    [
        (date(2025, 12, 31), D1V1.doc_id, D1V2.doc_id),  # the day before the new version
        (date(2026, 1, 1), D1V2.doc_id, D1V1.doc_id),  # valid_to is exclusive, valid_from is not
    ],
)
def test_the_version_boundary_is_half_open(
    db: Connection, as_of: date, present: str, absent: str
) -> None:
    found = docs(retrieve(db, "dividend records", ("cb",), as_of, embedder=HashEmbedder(),
                          options=WIDE))  # fmt: skip
    assert present in found
    assert absent not in found


def test_a_window_is_open_on_its_first_day_and_closed_on_its_last(db: Connection) -> None:
    def found(day: date) -> set[str]:
        return docs(retrieve(db, "provisioning", ("sfb",), day, embedder=HashEmbedder(),
                             options=WIDE))  # fmt: skip

    assert found(date(2025, 3, 14)) == set()
    assert found(date(2025, 3, 15)) == {D3.doc_id}
    assert found(date(2025, 9, 14)) == {D3.doc_id}
    assert found(date(2025, 9, 15)) == set()


def test_fragments_of_an_oversized_paragraph_collapse_to_their_parent(db: Connection) -> None:
    pieces = db.execute(
        "SELECT count(*) FROM chunks WHERE para_id = %s", (f"{D2.doc_id}#p7",)
    ).fetchone()
    assert pieces is not None
    assert pieces[0] > 1
    result = retrieve(db, "liquidity coverage each day", ("nbfc",), date(2026, 1, 1),
                      embedder=HashEmbedder(), options=WIDE)  # fmt: skip
    ids = [c.para_id for c in everything(result)]
    assert len(ids) == len(set(ids))
    parent = next(c for c in result.candidates if c.para_id == f"{D2.doc_id}#p7")
    assert parent.body == f"7. {OVERSIZED}"  # the whole paragraph, never a piece


def ranked(result: RetrievalResult) -> list[tuple[str, int | None, float | None]]:
    return [(c.para_id, c.rank, c.score) for c in result.candidates]


@pytest.mark.parametrize("query", QUERIES)
def test_expansion_adds_context_and_never_changes_the_ranked_list(
    db: Connection, query: str
) -> None:
    args = (db, query, ("cb", "ucb"), date(2026, 6, 1))
    narrow = RetrievalOptions(k=5, top_n=3)
    with_context = retrieve(*args, embedder=HashEmbedder(), options=narrow)
    no_expand = RetrievalOptions(k=5, top_n=3, expand=False)
    without = retrieve(*args, embedder=HashEmbedder(), options=no_expand)
    assert ranked(with_context) == ranked(without)
    assert without.context == []
    assert with_context.context
    by_id = {c.para_id: c for c in with_context.candidates}
    for c in with_context.context:
        assert c.expanded
        assert c.rank is None
        assert c.score is None
        assert c.para_id not in by_id
        origin = by_id[c.expanded_from or ""]
        assert c.doc_id == origin.doc_id
        assert abs(c.ordinal - origin.ordinal) == 1


class Tripwire:
    identity = HashEmbedder().identity

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        raise AssertionError("the reference path must not embed")


def test_a_reference_query_takes_the_fast_path_with_zero_network_calls(
    db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> httpx.Response:
        raise AssertionError("network call")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    result = retrieve(db, "Is DBOD.No. BP.BC.12/21.04.048/2014-15 still in force?",
                      ("cb",), date(2026, 6, 1), embedder=Tripwire())  # fmt: skip
    assert result.path == "reference"
    (match,) = result.references
    assert match.canonical_ref == REF
    assert match.consolidated_into == [D4.doc_id]
    assert [w["withdrawn_by_doc_id"] for w in match.withdrawals] == ["rbi:circ:2025:0100:v1"]
    assert result.candidates[0].para_id == f"{D4.doc_id}#p1"
    assert REF.split("/")[0].replace("DBOD.", "DBOD.No.") in result.candidates[0].body


def test_the_fast_path_obeys_the_prefilter_too(db: Connection) -> None:
    result = retrieve(db, f"what replaced {REF}?", ("sfb",), date(2025, 6, 1), embedder=Tripwire())
    assert result.path == "reference"
    assert result.candidates == []
    assert result.references[0].withdrawals  # the record still says it was withdrawn


def test_the_reader_role_can_retrieve(reader_dsn: str, index: str) -> None:
    with psycopg.connect(reader_dsn, autocommit=True) as reader:
        reader.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(index)))
        result = retrieve(reader, "dividend records", ("cb",), date(2026, 6, 1),
                          embedder=HashEmbedder())  # fmt: skip
        assert result.candidates
