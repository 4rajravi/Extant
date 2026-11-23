"""Retrieval: ranked candidate paragraphs for a query, an entity scope and an as-of date.

One public function, ``retrieve``. It never calls a generative model and never decides what
the answer is: it returns candidates. The connection's ``search_path`` names the build to
read (``SET search_path TO idx_rbi_..., public``), which is how serving rolls out a build.

Two paths.

The deterministic fast path. A query containing a literal circular or direction reference
skips semantic search. ``canonical_ref`` normalises the reference; it is looked up in
``withdrawals`` and ``concordance`` and, through ``ref_variants``, as a mention inside chunk
text. No embedding call is made, so the path costs nothing and cannot invent a match.

The hybrid path. One SQL statement filters ``chunks`` by entity scope and validity into a
``MATERIALIZED`` set first, and only then runs the dense arm (exact cosine distance, ascending)
and the lexical arm (descending score) over that set, each with its own ``LIMIT``. Filtering
before the limit is the property that matters: filtering after it is the failure this design
exists to avoid, where a selective filter silently empties the result. The two arms share one
``score`` column but sort in opposite directions, so each is ranked on its own, by ``src``,
before reciprocal rank fusion. Rank fusion, not score fusion: a distance and a cover-density
score are on incomparable scales, and normalising them would invent a comparison.

There is deliberately no vector index. Post-filtering an HNSW scan loses recall silently under
a selective filter; an iterative scan is right at scale but adds settings to tune; per-category
partial indexes duplicate multi-category provisions. Exact distance over the filtered set is
exact and has nothing to tune.

The lexical arm is ``ts_rank_cd``, cover density, which is weaker than BM25. It sits behind
``LexicalArm`` so a second implementation can replace it without touching fusion; which one
wins is a Phase 3 ablation, not decided here. The dense arm sits behind ``DenseArm`` for the
same reason: Phase 3 compares vector configurations the serving build does not store.

Fragments of a split paragraph collapse to their parent before anything is returned: a
candidate is always a paragraph the regulator numbered, with its whole body, never a piece.
Neighbourhood expansion then adds each result's ordinal neighbours in the same document,
marked ``expanded`` and unscored: context and citable, but never ranked, so expansion can
neither improve nor damage a recall number. Legal text puts the exemption next to the
obligation it guts; retrieving the obligation alone gives an answer that is quotable,
verifiable and wrong in effect.

Round trips. The design's component boundary says one SQL round trip; the hybrid path makes
three: the search, the hydration of the fused results with their parent paragraphs, and one
batched neighbourhood lookup. Fusion has to run between the first two, in Python, because it
needs both arms' ranks; hydrating only the fused top results keeps the search row narrow.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

import psycopg

from extant.core.identity import find_references, ref_variants
from extant.index.embed import Embedder, halfvec_literal, l2_normalise

# --------------------------------------------------------------------------- types


class LexicalArm(Protocol):
    """The lexical arm: a CTE body over ``allowed`` returning ``(chunk_id, score)``, best
    first (higher score is better), reading ``%(q)s`` and limited to ``%(k)s`` rows."""

    @property
    def name(self) -> str: ...

    def cte(self) -> str: ...


@dataclass(frozen=True)
class TsRankCd:
    name: str = "ts_rank_cd"

    def cte(self) -> str:
        # 'english' must match the configuration of the generated chunks.fts column.
        return (
            "SELECT a.chunk_id, ts_rank_cd(a.fts, q) AS score"
            " FROM allowed a, websearch_to_tsquery('english', %(q)s) AS q"
            " WHERE a.fts @@ q ORDER BY score DESC, a.chunk_id LIMIT %(k)s"
        )


class DenseArm(Protocol):
    """The dense arm: a CTE body over ``allowed`` returning ``(chunk_id, dist)``, best first
    (lower distance is better), reading the query vector ``%(qvec)s`` and limited to
    ``%(k)s`` rows. ``column`` is the ``chunks`` column it reads, carried into ``allowed`` as
    ``embedding``."""

    @property
    def name(self) -> str: ...

    @property
    def column(self) -> str: ...

    def cte(self) -> str: ...


_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")


@dataclass(frozen=True)
class ExactCosine:
    """Exact cosine distance over the filtered set. The serving default reads the build's
    ``halfvec(256)`` column; the Phase 3 ablations point it at other columns and types."""

    name: str = "exact_cosine"
    column: str = "embedding"
    vector_type: str = "halfvec"

    def __post_init__(self) -> None:
        # Both are interpolated into SQL, so both must be plain identifiers.
        for value in (self.column, self.vector_type):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"not an identifier: {value!r}")

    def cte(self) -> str:
        return (
            f"SELECT chunk_id, embedding <=> %(qvec)s::{self.vector_type} AS dist"  # noqa: S608
            " FROM allowed ORDER BY dist ASC, chunk_id LIMIT %(k)s"
        )


@dataclass(frozen=True)
class RetrievalOptions:
    k: int = 50  # rows each arm returns before fusion
    top_n: int = 10  # ranked paragraphs returned
    weights: tuple[float, float] = (1.0, 1.0)  # dense, lexical; 0 leaves an arm out of fusion
    rrf_k: int = 60
    expand: bool = True
    radius: int = 1  # ordinal neighbours either side
    lexical: LexicalArm = field(default_factory=TsRankCd)
    dense: DenseArm = field(default_factory=ExactCosine)
    # "pre" filters before either arm's LIMIT, which is the design. "post" exists for the
    # Phase 3 ablation only: each arm takes its LIMIT over the whole build and the filter
    # runs afterwards, the failure the design exists to avoid. Both modes return only rows
    # that pass the filter, so "post" loses recall and never leaks.
    filter_stage: Literal["pre", "post"] = "pre"
    # "in_force" is the design: only paragraphs valid on the as-of date. "any" drops the date
    # test (the entity test stays) and exists for one caller, the agent's supersession probe:
    # it fetches what the date filter hides so the gate can remove it on the record and say
    # what was repealed. Its rows are dropped before anything is composed.
    validity: Literal["in_force", "any"] = "in_force"


@dataclass(frozen=True)
class Candidate:
    para_id: str
    doc_id: str
    title: str
    heading_path: str | None
    body: str  # the whole paragraph as published, never a piece of it
    ordinal: int
    entity_codes: tuple[str, ...]
    valid_from: date
    valid_to: date | None
    authority_rank: int
    rank: int | None  # 1-based; None for an expanded neighbour
    score: float | None  # the fused score; None for an expanded neighbour
    arms: dict[str, int] = field(default_factory=dict)  # rank in each arm, best piece
    expanded: bool = False
    expanded_from: str | None = None
    # What a citation needs and the hot table does not carry: the regulator's own numbering
    # and where the paragraph is published. Added in Phase 4 for the agent's citations.
    para_number: str | None = None
    source_url: str = ""
    source_page: int | None = None


@dataclass(frozen=True)
class ReferenceMatch:
    surface: str
    canonical_ref: str
    withdrawals: list[dict[str, Any]]  # withdrawn_by_doc_id, effective_on, title, circular_date
    consolidated_into: list[str]  # doc_ids, from concordance


@dataclass
class RetrievalResult:
    path: Literal["reference", "hybrid"]
    candidates: list[Candidate]  # ranked
    context: list[Candidate]  # expanded neighbours, unranked
    references: list[ReferenceMatch]


# --------------------------------------------------------------------------- SQL

_ENTITY = "entity_codes && %(entity)s::text[]"
_IN_FORCE = " AND valid_from <= %(as_of)s AND (valid_to IS NULL OR valid_to > %(as_of)s)"
_FILTER = _ENTITY + _IN_FORCE
_FILTER_C = (
    "c.entity_codes && %(entity)s::text[]"
    " AND c.valid_from <= %(as_of)s"
    " AND (c.valid_to IS NULL OR c.valid_to > %(as_of)s)"
)
_ENTITY_C = "c.entity_codes && %(entity)s::text[]"


def _filter(validity: Literal["in_force", "any"], *, prefixed: bool = False) -> str:
    if validity == "any":
        return _ENTITY_C if prefixed else _ENTITY
    return _FILTER_C if prefixed else _FILTER


def search_sql(
    lexical: LexicalArm,
    dense: DenseArm | None = None,
    filter_stage: Literal["pre", "post"] = "pre",
    validity: Literal["in_force", "any"] = "in_force",
) -> str:
    """The hybrid search statement: filter first, materialised, then both arms. With
    ``filter_stage="post"``, the ablation: both arms over the whole build, filtered after."""
    arm = dense or ExactCosine()
    if not _IDENTIFIER.fullmatch(arm.column):
        raise ValueError(f"not an identifier: {arm.column!r}")
    # Only identifiers checked above, module constants and the arms' own SQL are
    # interpolated into these statements; every value is a parameter.
    columns = f"chunk_id, {arm.column} AS embedding, fts"
    if filter_stage == "pre":
        return (
            f"WITH allowed AS MATERIALIZED (SELECT {columns} FROM chunks"  # noqa: S608
            f" WHERE {_filter(validity)}),"
            f" dense AS ({arm.cte()}), lexical AS ({lexical.cte()})"
            " SELECT 'dense' AS src, chunk_id, dist AS score FROM dense"
            " UNION ALL SELECT 'lexical', chunk_id, score FROM lexical"
        )
    return (
        f"WITH allowed AS (SELECT {columns} FROM chunks),"  # noqa: S608
        f" dense AS ({arm.cte()}), lexical AS ({lexical.cte()})"
        " SELECT 'dense' AS src, x.chunk_id, x.dist AS score FROM dense x"
        f" JOIN chunks c ON c.chunk_id = x.chunk_id WHERE {_filter(validity, prefixed=True)}"
        " UNION ALL SELECT 'lexical', x.chunk_id, x.score FROM lexical x"
        f" JOIN chunks c ON c.chunk_id = x.chunk_id WHERE {_filter(validity, prefixed=True)}"
    )


_HYDRATE = (
    "SELECT c.chunk_id, c.para_id, p.doc_id, d.title, p.heading_path, p.body, p.ordinal,"
    " c.entity_codes, c.valid_from, c.valid_to, c.authority_rank,"
    " p.para_number, d.source_url, p.source_page"
    " FROM chunks c JOIN paragraphs p ON p.para_id = c.para_id"
    " JOIN documents d ON d.doc_id = p.doc_id WHERE c.chunk_id = ANY(%(ids)s)"
)
_NEIGHBOURS = (
    "SELECT DISTINCT ON (c.para_id) c.para_id, x.from_para, p.doc_id, d.title, p.heading_path,"
    " p.body, p.ordinal, c.entity_codes, c.valid_from, c.valid_to, c.authority_rank,"
    " p.para_number, d.source_url, p.source_page"
    " FROM unnest(%(docs)s::text[], %(ords)s::int[], %(froms)s::text[], %(ranks)s::int[])"
    "  AS x(doc_id, ordinal, from_para, from_rank)"
    " JOIN chunks c ON c.doc_id = x.doc_id"
    "  AND c.ordinal BETWEEN x.ordinal - %(r)s AND x.ordinal + %(r)s AND c.ordinal <> x.ordinal"
    " JOIN paragraphs p ON p.para_id = c.para_id JOIN documents d ON d.doc_id = p.doc_id"
    " WHERE c.entity_codes && %(entity)s::text[] AND c.valid_from <= %(as_of)s"
    " AND (c.valid_to IS NULL OR c.valid_to > %(as_of)s)"
    " ORDER BY c.para_id, x.from_rank"
)


def _mentions_sql(validity: Literal["in_force", "any"]) -> str:
    return (
        f"SELECT chunk_id FROM chunks WHERE {_filter(validity)}"  # noqa: S608
        " AND body ILIKE ANY(%(patterns)s)"
        " ORDER BY valid_from DESC, doc_id, ordinal, sub_index LIMIT %(k)s"
    )


_MENTIONS = _mentions_sql("in_force")


# --------------------------------------------------------------------------- pieces


def _rank_arms(rows: Sequence[tuple[str, int, float]]) -> dict[str, list[int]]:
    """Each arm ranked on its own: dense by distance ascending, lexical by score descending.
    Ties break on chunk_id so a ranking is reproducible."""
    dense = sorted((r for r in rows if r[0] == "dense"), key=lambda r: (r[2], r[1]))
    lexical = sorted((r for r in rows if r[0] == "lexical"), key=lambda r: (-r[2], r[1]))
    return {"dense": [r[1] for r in dense], "lexical": [r[1] for r in lexical]}


def _rrf(
    ranked_lists: Sequence[Sequence[int]], weights: Sequence[float], k: int = 60
) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: ``sum(w / (k + rank))`` per item, best first. An arm with
    weight 0 is left out entirely, so its rows cannot pad the list at a score of 0."""
    scores: dict[int, float] = defaultdict(float)
    for ranked, weight in zip(ranked_lists, weights, strict=True):
        if weight == 0:
            continue
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] += weight / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _candidate(row: Sequence[Any], **extra: Any) -> Candidate:
    para_id, doc_id, title, heading, body, ordinal, codes, vf, vt, rank_, number, url, page = row
    return Candidate(
        para_id=para_id,
        doc_id=doc_id,
        title=title,
        heading_path=heading,
        body=body,
        ordinal=ordinal,
        entity_codes=tuple(codes),
        valid_from=vf,
        valid_to=vt,
        authority_rank=int(rank_),
        para_number=number,
        source_url=str(url),
        source_page=page,
        **extra,
    )


def _collapse(
    conn: psycopg.Connection,
    fused: Sequence[tuple[int, float]],
    arms: dict[str, list[int]],
    top_n: int,
) -> list[Candidate]:
    """Fused chunk ids to ranked paragraphs: each split paragraph appears once, at the rank
    of its best piece, with its whole body."""
    if not fused:
        return []
    rows = conn.execute(_HYDRATE, {"ids": [cid for cid, _ in fused]}).fetchall()
    by_chunk = {r[0]: r[1:] for r in rows}
    arm_rank = {name: {cid: i for i, cid in enumerate(ids, start=1)} for name, ids in arms.items()}
    out: list[Candidate] = []
    seen: set[str] = set()
    for chunk_id, score in fused:
        row = by_chunk[chunk_id]
        if row[0] in seen:
            continue
        seen.add(row[0])
        ranks = {name: r[chunk_id] for name, r in arm_rank.items() if chunk_id in r}
        out.append(_candidate(row, rank=len(out) + 1, score=score, arms=ranks))
        if len(out) == top_n:
            break
    return out


def _expand(
    conn: psycopg.Connection,
    ranked: Sequence[Candidate],
    params: dict[str, Any],
    radius: int,
) -> list[Candidate]:
    if not ranked:
        return []
    rows = conn.execute(
        _NEIGHBOURS,
        {
            **params,
            "docs": [c.doc_id for c in ranked],
            "ords": [c.ordinal for c in ranked],
            "froms": [c.para_id for c in ranked],
            "ranks": [c.rank or 0 for c in ranked],
            "r": radius,
        },
    ).fetchall()
    ranked_ids = {c.para_id for c in ranked}
    out = [
        _candidate((r[0], *r[2:]), rank=None, score=None, expanded=True, expanded_from=r[1])
        for r in rows
        if r[0] not in ranked_ids
    ]
    return sorted(out, key=lambda c: (c.doc_id, c.ordinal))


_find_references = find_references  # the Phase 2 name; detection now lives in identity


# --------------------------------------------------------------------------- the function


def retrieve(
    conn: psycopg.Connection,
    query: str,
    entity_codes: Sequence[str],
    as_of: date,
    *,
    embedder: Embedder,
    options: RetrievalOptions | None = None,
) -> RetrievalResult:
    """Ranked candidate paragraphs in force on ``as_of`` for any of ``entity_codes``.

    ``entity_codes`` is matched by overlap with each chunk's codes, as given: a caller asking
    about an NBFC subtype passes the subtype and ``nbfc``. With ``validity="any"`` (the agent's
    supersession probe) the date test is dropped and nothing is expanded. Otherwise every
    candidate, ranked or expanded, on either path, satisfies both filters."""
    opts = options or RetrievalOptions()
    if not query.strip():
        raise ValueError("query is empty")
    if not entity_codes:
        raise ValueError("at least one entity code is required")
    params: dict[str, Any] = {"entity": list(entity_codes), "as_of": as_of, "k": opts.k}

    references = find_references(query)
    if references:
        return _reference_path(conn, references, params, opts)

    vector = l2_normalise(embedder.embed([query], kind="query").vectors[0])
    rows = conn.execute(
        search_sql(opts.lexical, opts.dense, opts.filter_stage, opts.validity),
        {**params, "q": query, "qvec": halfvec_literal(vector)},
    ).fetchall()
    arms = _rank_arms([(str(r[0]), int(r[1]), float(r[2])) for r in rows])
    fused = _rrf([arms["dense"], arms["lexical"]], opts.weights, opts.rrf_k)
    ranked = _collapse(conn, fused, arms, opts.top_n)
    expand = opts.expand and opts.validity == "in_force"
    context = _expand(conn, ranked, params, opts.radius) if expand else []
    return RetrievalResult("hybrid", ranked, context, [])


def _reference_path(
    conn: psycopg.Connection,
    references: Sequence[tuple[str, str]],
    params: dict[str, Any],
    opts: RetrievalOptions,
) -> RetrievalResult:
    canon = [c for _, c in references]
    withdrawn: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref, by, on, title, cdate in conn.execute(
        "SELECT canonical_ref, withdrawn_by_doc_id, effective_on, title, circular_date"
        " FROM withdrawals WHERE canonical_ref = ANY(%s)"
        " ORDER BY effective_on, withdrawn_by_doc_id",
        (canon,),
    ).fetchall():
        withdrawn[ref].append(
            {"withdrawn_by_doc_id": by, "effective_on": on, "title": title, "circular_date": cdate}
        )
    into: dict[str, list[str]] = defaultdict(list)
    for old, new in conn.execute(
        "SELECT old_ref, new_doc_id FROM concordance WHERE old_ref = ANY(%s)"
        " AND new_doc_id IS NOT NULL ORDER BY new_doc_id",
        (canon,),
    ).fetchall():
        into[old].append(new)
    matches = [
        ReferenceMatch(surface, c, withdrawn.get(c, []), into.get(c, []))
        for surface, c in references
    ]
    patterns = sorted({_like(v) for c in canon for v in ref_variants(c)})
    mentions = _mentions_sql(opts.validity)
    ids = [r[0] for r in conn.execute(mentions, {**params, "patterns": patterns}).fetchall()]
    targets = {d for m in matches for d in m.consolidated_into}
    rows = conn.execute(_HYDRATE, {"ids": ids}).fetchall() if ids else []
    order = {cid: i for i, cid in enumerate(ids)}
    rows.sort(key=lambda r: (r[2] not in targets, order[r[0]]))
    ranked: list[Candidate] = []
    seen: set[str] = set()
    for r in rows:
        if r[1] in seen:
            continue
        seen.add(r[1])
        ranked.append(_candidate(r[1:], rank=len(ranked) + 1, score=None, arms={"reference": 1}))
        if len(ranked) == opts.top_n:
            break
    expand = opts.expand and opts.validity == "in_force"
    context = _expand(conn, ranked, params, opts.radius) if expand else []
    return RetrievalResult("reference", ranked, context, matches)
