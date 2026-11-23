"""The database, as the agent's two reading nodes see it: an ``Index`` over one build.

``PgIndex`` wraps the Phase 2 retrieval library on a connection whose ``search_path`` names
the build, and turns its candidates into the agent's ``Candidate``, adding the fields a
citation needs. One embedding of a question serves both the ranked search and the
supersession probe. ``RecordedIndex`` replays what a ``RecordingIndex`` saw, so the graph is
tested end to end without a database or a network.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql

from extant.agent.models import Candidate, ReferenceRecord, WithdrawalRecord
from extant.agent.runtime import Index, Search
from extant.core.authority import AuthorityRank
from extant.index.embed import Embedded, Embedder, EmbeddingError, Kind, ModelIdentity
from extant.retrieval import Candidate as Retrieved
from extant.retrieval import RetrievalOptions, retrieve


def _binding(rank: int) -> bool:
    try:
        return AuthorityRank(rank).binding
    except ValueError:
        return True  # an unknown rank is treated as binding, never silently as advisory


def to_candidate(c: Retrieved, *, asked_for: date, origin: str) -> Candidate:
    expanded = origin == "expanded"
    return Candidate(
        para_id=c.para_id,
        doc_id=c.doc_id,
        doc_title=c.title,
        paragraph_number=c.para_number,
        heading_path=c.heading_path,
        body=c.body,
        ordinal=c.ordinal,
        rank=None if expanded else c.rank,
        fusion_score=None if expanded else c.score,
        lexical_rank=c.arms.get("lexical"),
        dense_rank=c.arms.get("dense"),
        expanded=expanded,
        expanded_from=c.expanded_from,
        origin=origin,  # type: ignore[arg-type]
        entity_scope=tuple(c.entity_codes),
        valid_from=c.valid_from,
        valid_to=c.valid_to,
        authority_rank=c.authority_rank,
        binding=_binding(c.authority_rank),
        source_url=c.source_url,
        source_page=c.source_page,
        asked_for=asked_for,
    )


class _Counting:
    """The real embedder, called at most once per question, with its tokens counted."""

    def __init__(self, inner: Embedder) -> None:
        self.inner = inner
        self.identity: ModelIdentity = inner.identity
        self.calls = 0
        self.tokens = 0
        self._memo: dict[str, list[float]] = {}

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        (text,) = texts
        if text not in self._memo:
            done = self.inner.embed([text], kind=kind)
            self.calls += 1
            self.tokens += done.tokens
            self._memo[text] = done.vectors[0]
        return Embedded([self._memo[text]], 0)


class _Unit:
    """A fixed vector for lexical-only retrieval: the dense arm still runs, weighted to 0."""

    def __init__(self, identity: ModelIdentity) -> None:
        self.identity = identity

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        return Embedded([[1.0] + [0.0] * (self.identity.dim - 1) for _ in texts], 0)


class _NoEmbedding:
    """The fast path's embedder: it must never be called."""

    def __init__(self, identity: ModelIdentity) -> None:
        self.identity = identity

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        raise AssertionError("the reference path made an embedding call")


class PgIndex:
    def __init__(self, conn: psycopg.Connection, schema: str, embedder: Embedder) -> None:
        self.conn = conn
        self.schema = schema
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        self._embedder = embedder
        self._counter: _Counting | None = None
        self._question: str | None = None

    @property
    def embedding_model(self) -> str:
        return self._embedder.identity.model_id

    def entity_codes(self) -> tuple[str, ...]:
        rows = self.conn.execute("SELECT code FROM entity_types ORDER BY code").fetchall()
        return tuple(str(r[0]) for r in rows)

    def entity_labels(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT code, label FROM entity_types ORDER BY code").fetchall()
        return {str(c): str(label) for c, label in rows}

    def search(
        self,
        query: str,
        entity_codes: tuple[str, ...],
        as_of: date,
        *,
        validity: Literal["in_force", "any"],
        top_n: int,
    ) -> Search:
        if self._question != query or self._counter is None:
            self._counter, self._question = _Counting(self._embedder), query
        before_calls, before_tokens = self._counter.calls, self._counter.tokens
        opts = RetrievalOptions(top_n=top_n, validity=validity, expand=validity == "in_force")
        degraded: list[str] = []
        try:
            result = retrieve(
                self.conn, query, entity_codes, as_of, embedder=self._counter, options=opts
            )
        except EmbeddingError:
            degraded = ["lexical_only"]
            lexical = RetrievalOptions(
                top_n=top_n, validity=validity, weights=(0.0, 1.0), expand=validity == "in_force"
            )
            result = retrieve(
                self.conn,
                query,
                entity_codes,
                as_of,
                embedder=_Unit(self._embedder.identity),
                options=lexical,
            )
        origin = "ranked" if validity == "in_force" else "probe"
        return Search(
            candidates=[to_candidate(c, asked_for=as_of, origin=origin) for c in result.candidates],
            neighbours=[
                to_candidate(c, asked_for=as_of, origin="expanded") for c in result.context
            ],
            embedding_calls=self._counter.calls - before_calls,
            embedding_tokens=self._counter.tokens - before_tokens,
            degraded=degraded,
        )

    def reference(
        self, query: str, entity_codes: tuple[str, ...], as_of: date
    ) -> tuple[list[Candidate], list[ReferenceRecord]]:
        opts = RetrievalOptions(top_n=5, validity="any", expand=False)
        result = retrieve(
            self.conn,
            query,
            entity_codes,
            as_of,
            embedder=_NoEmbedding(self._embedder.identity),
            options=opts,
        )
        if result.path != "reference":
            return [], []
        records = [
            ReferenceRecord(
                surface=m.surface,
                canonical_ref=m.canonical_ref,
                withdrawals=tuple(
                    WithdrawalRecord(
                        withdrawn_by_doc_id=w["withdrawn_by_doc_id"],
                        effective_on=w["effective_on"],
                        title=w["title"],
                        circular_date=w["circular_date"],
                    )
                    for w in m.withdrawals
                ),
                consolidated_into=tuple(m.consolidated_into),
            )
            for m in result.references
        ]
        found = [to_candidate(c, asked_for=as_of, origin="reference") for c in result.candidates]
        return found, records

    def version_at(self, para_id: str, day: date) -> Candidate | None:
        """The same numbered paragraph in the version of its direction in force on ``day``."""
        doc_id, anchor = para_id.split("#", 1)
        direction = doc_id.rsplit(":v", 1)[0]
        row = self.conn.execute(
            "SELECT c.chunk_id FROM chunks c WHERE c.doc_id LIKE %(d)s AND c.sub_index = 0"
            " AND split_part(c.para_id, '#', 2) = %(a)s AND c.valid_from <= %(day)s"
            " AND (c.valid_to IS NULL OR c.valid_to > %(day)s) ORDER BY c.valid_from DESC LIMIT 1",
            {"d": direction + ":v%", "a": anchor, "day": day},
        ).fetchone()
        if row is None:
            return None
        from extant.retrieval import _HYDRATE, _candidate

        (hit,) = self.conn.execute(_HYDRATE, {"ids": [row[0]]}).fetchall()
        return to_candidate(
            _candidate(hit[1:], rank=None, score=None), asked_for=day, origin="diff"
        )


# --------------------------------------------------------------------------- record and replay


def _key(*parts: object) -> str:
    return json.dumps([str(p) for p in parts])


@dataclass
class RecordingIndex:
    """Passes every call to a real index and keeps what it returned, for a fixture file."""

    inner: Index
    store: dict[str, Any] = field(default_factory=dict)

    @property
    def embedding_model(self) -> str:
        return self.inner.embedding_model

    def entity_codes(self) -> tuple[str, ...]:
        codes = self.inner.entity_codes()
        self.store["entity_codes"] = list(codes)
        return codes

    def search(
        self,
        query: str,
        entity_codes: tuple[str, ...],
        as_of: date,
        *,
        validity: Literal["in_force", "any"],
        top_n: int,
    ) -> Search:
        found = self.inner.search(query, entity_codes, as_of, validity=validity, top_n=top_n)
        self.store[_key("search", query, entity_codes, as_of, validity, top_n)] = {
            "candidates": [c.model_dump(mode="json") for c in found.candidates],
            "neighbours": [c.model_dump(mode="json") for c in found.neighbours],
            "embedding_calls": found.embedding_calls,
            "embedding_tokens": found.embedding_tokens,
            "degraded": found.degraded,
        }
        return found

    def reference(
        self, query: str, entity_codes: tuple[str, ...], as_of: date
    ) -> tuple[list[Candidate], list[ReferenceRecord]]:
        cands, refs = self.inner.reference(query, entity_codes, as_of)
        self.store[_key("reference", query, entity_codes, as_of)] = {
            "candidates": [c.model_dump(mode="json") for c in cands],
            "references": [r.model_dump(mode="json") for r in refs],
        }
        return cands, refs

    def version_at(self, para_id: str, day: date) -> Candidate | None:
        found = self.inner.version_at(para_id, day)
        self.store[_key("version_at", para_id, day)] = (
            None if found is None else found.model_dump(mode="json")
        )
        return found


class RecordedIndex:
    """Serves what a ``RecordingIndex`` kept. No database, no network."""

    def __init__(self, store: dict[str, Any], embedding_model: str) -> None:
        self.store = store
        self._embedding_model = embedding_model

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    def _get(self, key: str) -> Any:
        if key not in self.store:
            raise LookupError(f"no recorded index call {key}; re-record the fixture")
        return self.store[key]

    def entity_codes(self) -> tuple[str, ...]:
        return tuple(self._get("entity_codes"))

    def search(
        self,
        query: str,
        entity_codes: tuple[str, ...],
        as_of: date,
        *,
        validity: Literal["in_force", "any"],
        top_n: int,
    ) -> Search:
        got = self._get(_key("search", query, entity_codes, as_of, validity, top_n))
        return Search(
            candidates=[Candidate.model_validate(c) for c in got["candidates"]],
            neighbours=[Candidate.model_validate(c) for c in got["neighbours"]],
            embedding_calls=got["embedding_calls"],
            embedding_tokens=got["embedding_tokens"],
            degraded=list(got["degraded"]),
        )

    def reference(
        self, query: str, entity_codes: tuple[str, ...], as_of: date
    ) -> tuple[list[Candidate], list[ReferenceRecord]]:
        got = self._get(_key("reference", query, entity_codes, as_of))
        return (
            [Candidate.model_validate(c) for c in got["candidates"]],
            [ReferenceRecord.model_validate(r) for r in got["references"]],
        )

    def version_at(self, para_id: str, day: date) -> Candidate | None:
        got = self._get(_key("version_at", para_id, day))
        return None if got is None else Candidate.model_validate(got)


def load_fixture(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
