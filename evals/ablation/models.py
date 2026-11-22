"""The two local models, pinned to the Hugging Face revisions the ablation was run with."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from extant.index.embed import Embedded, Kind, ModelIdentity
from extant.retrieval import Candidate

# Repository and the exact commit downloaded, so a rerun loads the same weights.
BGE_M3 = ("BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181")
BGE_RERANKER = ("BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")


def _device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


class LocalEmbedder:
    """BGE-M3 dense embeddings, 1024 dimensions, unit length. No query instruction: BGE-M3
    embeds queries and passages the same way."""

    def __init__(
        self, name: str = "bge-m3", dim: int = 1024, *, quantisation: str = "float16"
    ) -> None:
        if name != "bge-m3" or dim != 1024:
            raise ValueError(f"no local model {name} at {dim} dimensions")
        from sentence_transformers import SentenceTransformer

        repo, revision = BGE_M3
        self.identity = ModelIdentity(f"local/{name}", dim, quantisation)
        self.model: Any = SentenceTransformer(repo, revision=revision, device=_device())
        # Chunks are cut at 900 estimated tokens; XLM-R tokenises legal English a little finer.
        self.model.max_seq_length = 1024

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        vectors = self.model.encode(
            list(texts), batch_size=8, normalize_embeddings=True, convert_to_numpy=True
        )
        return Embedded([[float(x) for x in v] for v in vectors], 0)


class CrossEncoderReranker:
    """bge-reranker-v2-m3 over (query, paragraph) pairs. Pairs are cut at 512 tokens, which
    keeps a run on the Mac to minutes; a longer paragraph is judged on its opening."""

    def __init__(self, max_length: int = 512) -> None:
        from sentence_transformers import CrossEncoder

        repo, revision = BGE_RERANKER
        self.model: Any = CrossEncoder(
            repo, revision=revision, max_length=max_length, device=_device()
        )

    def rerank(self, query: str, candidates: Sequence[Candidate]) -> list[Candidate]:
        if not candidates:
            return []
        scores = self.model.predict([(query, c.body) for c in candidates], batch_size=16)
        order = sorted(range(len(candidates)), key=lambda i: (-float(scores[i]), i))
        return [
            replace(candidates[i], rank=rank, score=float(scores[i]))
            for rank, i in enumerate(order, start=1)
        ]
