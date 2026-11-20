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
wins is a Phase 3 ablation, not decided here.

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

from extant.core.identity import InvalidReferenceError, canonical_ref, ref_variants
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


@dataclass(frozen=True)
class RetrievalOptions:
    k: int = 50  # rows each arm returns before fusion
    top_n: int = 10  # ranked paragraphs returned
    weights: tuple[float, float] = (1.0, 1.0)  # dense, lexical
    rrf_k: int = 60
    expand: bool = True
    radius: int = 1  # ordinal neighbours either side
    lexical: LexicalArm = field(default_factory=TsRankCd)


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

_FILTER = (
    "entity_codes && %(entity)s::text[]"
    " AND valid_from <= %(as_of)s"
    " AND (valid_to IS NULL OR valid_to > %(as_of)s)"
)
# Only module constants are interpolated into these statements; every value is a parameter.
_ALLOWED = f"allowed AS MATERIALIZED (SELECT chunk_id, embedding, fts FROM chunks WHERE {_FILTER})"  # noqa: S608
_DENSE = (
    "SELECT chunk_id, embedding <=> %(qvec)s::halfvec AS dist"
    " FROM allowed ORDER BY dist ASC, chunk_id LIMIT %(k)s"
)


def search_sql(lexical: LexicalArm) -> str:
    """The hybrid search statement: filter first, materialised, then both arms."""
    # Constants and the arm's own SQL are interpolated; every value is a parameter.
    return (
        f"WITH {_ALLOWED}, dense AS ({_DENSE}), lexical AS ({lexical.cte()})"  # noqa: S608
        " SELECT 'dense' AS src, chunk_id, dist AS score FROM dense"
        " UNION ALL SELECT 'lexical', chunk_id, score FROM lexical"
    )


_HYDRATE = (
    "SELECT c.chunk_id, c.para_id, p.doc_id, d.title, p.heading_path, p.body, p.ordinal,"
    " c.entity_codes, c.valid_from, c.valid_to, c.authority_rank"
    " FROM chunks c JOIN paragraphs p ON p.para_id = c.para_id"
    " JOIN documents d ON d.doc_id = p.doc_id WHERE c.chunk_id = ANY(%(ids)s)"
)
_NEIGHBOURS = (
    "SELECT DISTINCT ON (c.para_id) c.para_id, x.from_para, p.doc_id, d.title, p.heading_path,"
    " p.body, p.ordinal, c.entity_codes, c.valid_from, c.valid_to, c.authority_rank"
    " FROM unnest(%(docs)s::text[], %(ords)s::int[], %(froms)s::text[], %(ranks)s::int[])"
    "  AS x(doc_id, ordinal, from_para, from_rank)"
    " JOIN chunks c ON c.doc_id = x.doc_id"
    "  AND c.ordinal BETWEEN x.ordinal - %(r)s AND x.ordinal + %(r)s AND c.ordinal <> x.ordinal"
    " JOIN paragraphs p ON p.para_id = c.para_id JOIN documents d ON d.doc_id = p.doc_id"
    " WHERE c.entity_codes && %(entity)s::text[] AND c.valid_from <= %(as_of)s"
    " AND (c.valid_to IS NULL OR c.valid_to > %(as_of)s)"
    " ORDER BY c.para_id, x.from_rank"
)
_MENTIONS = (
    f"SELECT chunk_id FROM chunks WHERE {_FILTER} AND body ILIKE ANY(%(patterns)s)"  # noqa: S608
    " ORDER BY valid_from DESC, doc_id, ordinal, sub_index LIMIT %(k)s"
)


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
    """Reciprocal rank fusion: ``sum(w / (k + rank))`` per item, best first."""
    scores: dict[int, float] = defaultdict(float)
    for ranked, weight in zip(ranked_lists, weights, strict=True):
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] += weight / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


_REF_TOKEN = re.compile(r"(?<![\w/])[A-Za-z][\w.()&-]*/[\w.()&/-]+")
_SPACE_AFTER_NO = re.compile(r"\b(No\.)\s+(?=[\w(])", re.IGNORECASE)


def _find_references(query: str) -> list[tuple[str, str]]:
    """``(surface, canonical)`` for every literal reference in the query: a token that starts
    with a letter, contains a slash and a digit, and that ``canonical_ref`` accepts."""
    found: dict[str, str] = {}
    for m in _REF_TOKEN.finditer(_SPACE_AFTER_NO.sub(r"\1", query)):
        token = m.group(0).rstrip(".,;:")
        if token.count("(") < token.count(")"):
            token = token.rstrip(")")
        if len(token) < 8 or not any(ch.isdigit() for ch in token):
            continue
        try:
            canonical = canonical_ref(token)
        except InvalidReferenceError:
            continue
        if "/" in canonical and canonical not in found.values():
            found[token] = canonical
    return list(found.items())


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _candidate(row: Sequence[Any], **extra: Any) -> Candidate:
    para_id, doc_id, title, heading, body, ordinal, codes, vf, vt, rank_ = row
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
    about an NBFC subtype passes the subtype and ``nbfc``. Every candidate, ranked or
    expanded, on either path, satisfies both filters."""
    opts = options or RetrievalOptions()
    if not query.strip():
        raise ValueError("query is empty")
    if not entity_codes:
        raise ValueError("at least one entity code is required")
    params: dict[str, Any] = {"entity": list(entity_codes), "as_of": as_of, "k": opts.k}

    references = _find_references(query)
    if references:
        return _reference_path(conn, references, params, opts)

    vector = l2_normalise(embedder.embed([query], kind="query").vectors[0])
    rows = conn.execute(
        search_sql(opts.lexical), {**params, "q": query, "qvec": halfvec_literal(vector)}
    ).fetchall()
    arms = _rank_arms([(str(r[0]), int(r[1]), float(r[2])) for r in rows])
    fused = _rrf([arms["dense"], arms["lexical"]], opts.weights, opts.rrf_k)
    ranked = _collapse(conn, fused, arms, opts.top_n)
    context = _expand(conn, ranked, params, opts.radius) if opts.expand else []
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
    ids = [r[0] for r in conn.execute(_MENTIONS, {**params, "patterns": patterns}).fetchall()]
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
    context = _expand(conn, ranked, params, opts.radius) if opts.expand else []
    return RetrievalResult("reference", ranked, context, matches)
