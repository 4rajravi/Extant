"""The reduced corpus: narrowed build inputs keep every reference inside the sample, and the
committed sample keeps every gold document of its evaluated queries."""

from __future__ import annotations

from evals.ablation.sample_index import sample_inputs
from evals.sets import read_sample, sample_queries
from tests.integration.synthetic import D1V1, D1V2, D2, build_inputs


def test_narrowed_inputs_keep_only_the_sample_and_no_dangling_reference() -> None:
    docs = {D1V2.doc_id, D2.doc_id}  # D1V2 supersedes D1V1, which is left out
    narrowed = sample_inputs(build_inputs(), docs)
    assert {d.doc_id for d in narrowed.documents} == docs
    assert all(
        d.supersedes_doc_id is None or d.supersedes_doc_id in docs for d in narrowed.documents
    )
    assert {p.doc_id for p in narrowed.paragraphs} == docs
    assert {c.doc_id for c in narrowed.chunks} == docs
    paras = {p.para_id for p in narrowed.paragraphs}
    for f in narrowed.facets:
        assert (f.ref_id in docs) if f.scope == "document" else (f.ref_id in paras)
    assert D1V1.doc_id not in {c.doc_id for c in narrowed.chunks}
    assert "sample" in narrowed.manifest


def test_every_gold_document_of_the_sampled_queries_is_in_the_sample() -> None:
    sample = read_sample()
    corpus = set(sample["gold_documents"]) | set(sample["distractor_documents"])
    queries = sample_queries()
    dropped = set(sample["queries"]) - {q.query_id for q in queries}
    assert all(qid.endswith("-new") for qid in dropped)  # only deleted provisions' new side
    assert len(dropped) <= 3
    for q in queries:
        assert set(q.gold_doc_ids) <= corpus, q.query_id
        assert q.split == "dev"
    assert not set(sample["gold_documents"]) & set(sample["distractor_documents"])
