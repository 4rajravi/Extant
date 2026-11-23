"""The agent's two database nodes against a real build of the synthetic corpus."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest

from extant.agent.graph import ask, build
from extant.agent.index import PgIndex
from extant.agent.models import RefusalReason
from extant.agent.runtime import Deps
from extant.core.identity import make_schema_name
from extant.core.schema import drop, schema_exists
from extant.index.assemble import assemble
from extant.index.embed import HashEmbedder, embed_missing, plan
from extant.retrieval import RetrievalOptions, retrieve
from tests.agent_support import composer_says, judge_says, parse_says
from tests.integration.synthetic import D1V1, D1V2, D3, D4, REF, build_inputs

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def schema(writer_dsn: str, bootstrapped: None) -> Iterator[str]:
    inputs = build_inputs()
    name = make_schema_name("test", date.today(), secrets.token_hex(4))
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        e = HashEmbedder()
        todo = plan(
            c, {x.content_hash: x.embed_text for x in inputs.chunks}, e.identity, eur_per_usd=0.9
        )
        embed_missing(c, e, todo.misses)
        assemble(c, name, inputs, e.identity, baseline=None)
    yield name
    with psycopg.connect(writer_dsn, autocommit=True) as c:
        if schema_exists(c, name):
            drop(c, name)


@pytest.fixture
def index(reader_dsn: str, schema: str) -> Iterator[PgIndex]:
    with psycopg.connect(reader_dsn, autocommit=True) as conn:
        yield PgIndex(conn, schema, HashEmbedder())


SPELLINGS = (
    "DBOD.No.BP.BC.12/21.04.048/2014-15",
    "DBOD.No. BP.BC.12/21.04.048/2014-15",
    "dbod.no.bp.bc.12/21.04.048/2014-15",
    "DBOD.BP.BC.No.12/21.04.048/2014-15",
    "DBOD.BP.BC.No. 12/21.04.048/2014-15",
    "(DBOD.No.BP.BC.12/21.04.048/2014-15)",
)


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_reference_in_six_published_spellings_resolves_to_one_document(
    index: PgIndex, spelling: str
) -> None:
    cands, refs = index.reference(f"What replaced circular {spelling}?", ("cb",), date(2026, 6, 1))
    assert [r.canonical_ref for r in refs] == [REF]
    assert {c.doc_id for c in cands} == {D4.doc_id}
    assert refs[0].consolidated_into == (D4.doc_id,)


@pytest.mark.xfail(
    strict=True,
    reason="the question tokeniser misses references with spaces"
    " inside them or no dot after the department, the same gap Phase 3 measured"
    " on the withdrawal set; not fixed in Phase 4",
)
@pytest.mark.parametrize(
    "spelling",
    [
        "DBOD. No. BP. BC. 12/21.04.048/2014-15",
        "DBOD.No.BP.BC.12 /21.04.048 /2014-15",
        "DBOD No.BP.BC.12/21.04.048/2014-15",
    ],
)
def test_spellings_the_detector_does_not_yet_read(index: PgIndex, spelling: str) -> None:
    _, refs = index.reference(f"What replaced circular {spelling}?", ("cb",), date(2026, 6, 1))
    assert [r.canonical_ref for r in refs] == [REF]


def test_the_fast_path_end_to_end_makes_no_model_or_embedding_call(index: PgIndex) -> None:
    p, c, j = parse_says(), composer_says(), judge_says()
    graph = build(Deps(models={"parse": p, "compose": c, "judge": j}, index=index))
    state = ask(
        graph,
        f"What replaced circular {SPELLINGS[0]}?",
        index_version=index.schema,
        entity="cb",
        as_of=date(2026, 6, 1),
        today=date(2026, 11, 22),
    )
    assert state.outcome == "answer"
    assert (p.calls, c.calls, j.calls) == ([], [], [])
    assert sum(n.calls + n.embedding_calls for n in state.node_log) == 0
    (claim,) = state.verified_claims
    assert claim.citations[0].doc_id == D4.doc_id


def test_a_selective_filter_still_returns_k_results_which_a_post_filter_cannot(
    index: PgIndex,
) -> None:
    on = date(2025, 6, 1)  # only D3 is in force for sfb, and only between March and September
    found = index.search("provisioning decisions", ("sfb",), on, validity="in_force", top_n=6)
    assert len(found.candidates) == 6
    assert {c.doc_id for c in found.candidates} == {D3.doc_id}
    post = retrieve(
        index.conn,
        "provisioning decisions",
        ("sfb",),
        on,
        embedder=HashEmbedder(),
        options=RetrievalOptions(k=6, top_n=6, filter_stage="post"),
    )
    assert len(post.candidates) < 6


def test_the_probe_finds_what_the_date_filter_hides(index: PgIndex) -> None:
    on = date(2026, 6, 1)  # D1 v1 ended on 2026-01-01
    live = index.search("dividend records", ("cb",), on, validity="in_force", top_n=20)
    probe = index.search("dividend records", ("cb",), on, validity="any", top_n=20)
    assert D1V1.doc_id not in {c.doc_id for c in live.candidates}
    assert D1V1.doc_id in {c.doc_id for c in probe.candidates}
    assert probe.embedding_calls == 0  # one embedding of the question serves both
    assert all(c.origin == "probe" and not c.expanded for c in probe.candidates)


def test_version_at_finds_the_same_paragraph_in_the_other_version(index: PgIndex) -> None:
    other = index.version_at(f"{D1V1.doc_id}#p1", date(2026, 6, 1))
    assert other is not None
    assert other.para_id == f"{D1V2.doc_id}#p1"
    assert other.asked_for == date(2026, 6, 1)
    assert index.version_at(f"{D1V1.doc_id}#p1", date(2020, 1, 1)) is None


def test_on_a_real_build_the_gate_drops_the_old_version_and_it_never_reaches_compose(
    index: PgIndex,
) -> None:
    claim = ("Records are reported every month.", [0], ["report them every month"])
    composer = composer_says(claim)
    graph = build(
        Deps(
            models={"parse": parse_says(), "compose": composer, "judge": judge_says()}, index=index
        )
    )
    state = ask(
        graph,
        "dividend records reporting",
        index_version=index.schema,
        entity="cb",
        as_of=date(2026, 6, 1),
        today=date(2026, 11, 22),
    )
    (_, _, prompt) = composer.calls[0]
    # The probe found the old version and the gate set it aside, on the record...
    assert any(c.doc_id == D1V1.doc_id for c in state.set_aside)
    assert {d.reason for d in state.dropped} == {"superseded"}
    # ...so the composer was shown exactly the surviving candidates, none of them stale.
    # (Bodies are not compared: D4 repeats some of D1 v1's paragraphs word for word.)
    assert D1V1.doc_id not in {c.doc_id for c in state.candidates}
    assert prompt.count("\n[") + prompt.startswith("[") == len(state.candidates)
    assert all(c.in_force() for c in state.candidates)
    assert state.refusal_reason in (None, RefusalReason.VERIFICATION_FAILED)
