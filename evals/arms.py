"""The six Phase 3 retrieval ablations, as configurations of the Phase 2 retrieval library.

Arms 1 to 3 run on the full index. Arms 4 to 6 re-embed or rerank, so they run on a reduced
corpus (``evals/samples/rbi-sample.json``) whose build carries the extra vector columns, and
each is compared only with the reference arm on the same sample.

1. Retrieval mode: hybrid against dense only and lexical only.
2. Prefilter: the hard prefilter against filtering after each arm's LIMIT.
3. Neighbourhood expansion: on against off. Expansion never changes the ranked list by design,
   so it can only show up in the "with context" recall columns.
4. Cross-encoder reranker over the hybrid top 50: offline only, never shipped.
5. Hosted against local embeddings: text-embedding-3-large at 256 against BGE-M3.
6. Vector precision: 1024 dimensions float32 against 256 float16 against binary with
   rescoring. pgvector has no int8 vector type, so float16 stands where the design says int8.

The two answer level arms (the supersession gate, citation verification) are Phase 6.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from extant.index.embed import ModelIdentity
from extant.retrieval import ExactCosine, RetrievalOptions

TOP_N = 20  # the deepest recall depth reported

HOSTED_256 = ModelIdentity("openai/text-embedding-3-large", 256, "float16")
HOSTED_1024 = ModelIdentity("openai/text-embedding-3-large", 1024, "float32")
LOCAL_BGE_M3 = ModelIdentity("local/bge-m3", 1024, "float16")

# Columns the reduced-corpus build adds beside the serving column `embedding`.
SAMPLE_COLUMNS = {
    "emb_oai1024": ("vector", HOSTED_1024),
    "emb_bgem3": ("halfvec", LOCAL_BGE_M3),
}


@dataclass(frozen=True)
class BinaryRescore:
    """Hamming distance over ``binary_quantize`` of the 1024 float32 column picks a shortlist
    of ``oversample`` times k, which exact cosine on the full vectors then reorders."""

    name: str = "binary_rescore"
    column: str = "emb_oai1024"
    dims: int = 1024
    oversample: int = 4

    def cte(self) -> str:
        # Only integer fields of this frozen class are interpolated; values are parameters.
        return (
            "SELECT chunk_id, embedding <=> %(qvec)s::vector AS dist FROM ("  # noqa: S608
            f" SELECT chunk_id, embedding FROM allowed ORDER BY"
            f" binary_quantize(embedding)::bit({self.dims})"
            f" <~> binary_quantize(%(qvec)s::vector)::bit({self.dims}), chunk_id"
            f" LIMIT %(k)s * {self.oversample}) shortlist"
            " ORDER BY dist ASC, chunk_id LIMIT %(k)s"
        )


@dataclass(frozen=True)
class Arm:
    name: str
    ablation: str  # the comparison it belongs to
    description: str
    corpus: Literal["full", "sample"]
    options: RetrievalOptions
    query_model: ModelIdentity = HOSTED_256
    rerank: bool = False
    reference: bool = False  # the arm the others in its ablation are compared with
    compare_with: str | None = None  # that reference arm, on the same corpus


_BASE = RetrievalOptions(top_n=TOP_N)
_DENSE = replace(_BASE, weights=(1.0, 0.0), expand=False)

ARMS: tuple[Arm, ...] = (
    Arm("hybrid", "baseline", "Phase 2 retrieval as shipped: prefilter, dense and"
        " ts_rank_cd fused by RRF, expansion on", "full", _BASE, reference=True),
    Arm("dense-only", "1 retrieval mode", "dense arm alone", "full",
        replace(_BASE, weights=(1.0, 0.0)), compare_with="hybrid"),
    Arm("lexical-only", "1 retrieval mode", "ts_rank_cd alone", "full",
        replace(_BASE, weights=(0.0, 1.0)), compare_with="hybrid"),
    Arm("post-filter", "2 prefilter", "each arm takes its 50 over the whole build, then the"
        " entity and date filter", "full", replace(_BASE, filter_stage="post"),
        compare_with="hybrid"),
    Arm("no-expansion", "3 expansion", "neighbourhood expansion off", "full",
        replace(_BASE, expand=False), compare_with="hybrid"),
    Arm("s-hybrid", "4 reranker", "hybrid on the reduced corpus", "sample",
        replace(_BASE, expand=False), reference=True),
    Arm("s-hybrid-rerank", "4 reranker", "hybrid top 50 reordered by bge-reranker-v2-m3",
        "sample", replace(_BASE, top_n=50, expand=False), rerank=True, compare_with="s-hybrid"),
    Arm("s-dense-256-f16", "5 and 6 embeddings", "text-embedding-3-large, 256 dims, float16,"
        " dense only", "sample", _DENSE, reference=True),
    Arm("s-dense-1024-f32", "6 precision", "text-embedding-3-large, 1024 dims, float32,"
        " dense only", "sample",
        replace(_DENSE, dense=ExactCosine(column="emb_oai1024", vector_type="vector")),
        query_model=HOSTED_1024, compare_with="s-dense-256-f16"),
    Arm("s-dense-1024-binary", "6 precision", "1024 dims, binary quantised shortlist of 200"
        " rescored with float32, dense only", "sample",
        replace(_DENSE, dense=BinaryRescore()), query_model=HOSTED_1024,
        compare_with="s-dense-256-f16"),
    Arm("s-dense-bge-m3", "5 local", "BGE-M3 on the Mac, 1024 dims, float16, dense only",
        "sample", replace(_DENSE, dense=ExactCosine(column="emb_bgem3", vector_type="halfvec")),
        query_model=LOCAL_BGE_M3, compare_with="s-dense-256-f16"),
)  # fmt: skip

BY_NAME = {a.name: a for a in ARMS}
GATED = "hybrid"  # the arm the CI gate measures
