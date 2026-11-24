"""Lookups over one build schema that are not ranked retrieval.

A paragraph by its id, the directions in force for an entity on a date, a circular reference
resolved, a direction's version history and the change between two of its versions, the
build's manifest and its entity taxonomy. Added in Phase 7 because the MCP server's tools need
them and the server must hold no logic of its own: it validates, calls one of these, and
shapes the result. Like ``extant.retrieval`` they take a connection whose ``search_path``
names the build, and every query applies the trust filter.

The version history is partial and says so. The index holds the consolidated directions from
November 2025 onwards, version by version, but a version RBI published only as a PDF is not
in it (30 directions skip a version number). A diff never compares a version with itself and
never reports "no change" across a gap it cannot see: a date outside the covered span, or one
where a missing version may have been in force, is stated as such.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from extant.core.authority import AuthorityRank
from extant.core.identity import PARA_ID_RE, find_references
from extant.index.embed import Embedded, Kind, ModelIdentity
from extant.retrieval import RetrievalOptions, retrieve

# A paragraph replaced by a deletion marker ("23. *****" or "[***]") in a later version.
_DELETED = re.compile(r"^\s*[0-9A-Za-z().]*\s*\[?\*{3,}\]?\s*$")
_WORD = re.compile(r"[a-z0-9]+")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _binding(rank: int) -> bool:
    try:
        return AuthorityRank(rank).binding
    except ValueError:
        return True  # an unknown rank is treated as binding, never silently as advisory


# --------------------------------------------------------------------------- a paragraph


class ParagraphRecord(_Model):
    para_id: str
    doc_id: str
    doc_title: str
    paragraph_number: str | None
    heading_path: str | None
    body: str
    entity_scope: list[str]
    in_force_from: date
    in_force_to: date | None  # the first day it no longer applies; None while in force
    authority_rank: int
    binding: bool
    source_url: str
    source_page: int | None
    resolved_from: str | None = None  # the retired para_id it was asked by, if any
    resolution_reason: str | None = None


_PARAGRAPH = (
    "SELECT p.para_id, p.doc_id, d.title, p.para_number, p.heading_path, p.body,"
    " min(c.valid_from), max(c.valid_to), bool_or(c.valid_to IS NULL),"
    " (SELECT array_agg(DISTINCT e ORDER BY e) FROM chunks c2, unnest(c2.entity_codes) e"
    "  WHERE c2.para_id = p.para_id AND c2.trusted), d.authority_rank, d.source_url,"
    " p.source_page"
    " FROM paragraphs p JOIN documents d ON d.doc_id = p.doc_id"
    " JOIN chunks c ON c.para_id = p.para_id AND c.trusted"
    " WHERE p.para_id = %s GROUP BY p.para_id, d.doc_id"
)


def _paragraph_record(row: Sequence[Any], **extra: Any) -> ParagraphRecord:
    (pid, doc, title, num, heading, body, vf, vt, open_ended, ents, rank, url, page) = row
    return ParagraphRecord(
        para_id=pid,
        doc_id=doc,
        doc_title=title,
        paragraph_number=num,
        heading_path=heading,
        body=body,
        entity_scope=list(ents),
        in_force_from=vf,
        in_force_to=None if open_ended else vt,
        authority_rank=rank,
        binding=_binding(rank),
        source_url=url,
        source_page=page,
        **extra,
    )


def fetch_paragraph(conn: Any, para_id: str) -> ParagraphRecord | None:
    """The paragraph with its metadata, or None. A para_id retired by a reparse resolves
    through ``para_id_aliases`` to the paragraph that replaced it, and says so."""
    if not PARA_ID_RE.fullmatch(para_id):
        raise ValueError(f"not a paragraph identifier: {para_id!r}")
    row = conn.execute(_PARAGRAPH, (para_id,)).fetchone()
    if row is not None:
        return _paragraph_record(row)
    alias = conn.execute(
        "SELECT new_para_id, reason FROM para_id_aliases WHERE old_para_id = %s", (para_id,)
    ).fetchone()
    if alias is None or alias[0] is None:
        return None
    row = conn.execute(_PARAGRAPH, (alias[0],)).fetchone()
    if row is None:
        return None
    return _paragraph_record(row, resolved_from=para_id, resolution_reason=str(alias[1]))


def record_alias_hit(conn: Any, old_para_id: str, new_para_id: str, index_version: str) -> int:
    """Count one resolution of a retired id through an alias, in ``public.para_id_alias_hits``
    (kept across builds), and return the count for this build. The one write in this module:
    a citation endpoint calls it each time ``fetch_paragraph`` resolved through an alias."""
    row = conn.execute(
        "INSERT INTO public.para_id_alias_hits AS h (old_para_id, index_version, new_para_id,"
        " hits) VALUES (%s, %s, %s, 1) ON CONFLICT (old_para_id, index_version) DO UPDATE"
        " SET hits = h.hits + 1, last_hit_at = now(), new_para_id = EXCLUDED.new_para_id"
        " RETURNING hits",
        (old_para_id, index_version, new_para_id),
    ).fetchone()
    return int(row[0])


# --------------------------------------------------------------------------- directions in force


class DirectionRecord(_Model):
    doc_id: str
    title: str
    instrument_type: str
    authority_rank: int
    binding: bool
    in_force_from: date
    in_force_to: date | None
    paragraphs_in_force: int
    entity_scope: list[str]
    source_url: str
    topic_score: float | None  # None when no topic was given


def _or_query(topic: str) -> str:
    """A tsquery matching any word of the topic: a topic is a few words, not a sentence, so
    any match ranks instead of every word being required."""
    words = _WORD.findall(topic.lower())
    if not words:
        raise ValueError("the topic has no searchable words")
    return " | ".join(words)


def directions_in_force(
    conn: Any, entity_codes: Sequence[str], as_of: date, topic: str | None = None
) -> list[DirectionRecord]:
    """Every direction with a paragraph in force for one of ``entity_codes`` on ``as_of``.
    With a topic, only those whose in-force text matches it, best first, by lexical rank (no
    embedding call); without one, the full list by title."""
    params: dict[str, Any] = {"entity": list(entity_codes), "as_of": as_of}
    score, having = "NULL::real", ""
    if topic is not None:
        params["q"] = _or_query(topic)
        score = "max(ts_rank_cd(c.fts, to_tsquery('english', %(q)s)))"
        having = f" HAVING {score} > 0"
    sql = (
        "SELECT d.doc_id, d.title, d.instrument_type, d.authority_rank,"  # noqa: S608
        " min(c.valid_from), max(c.valid_to), bool_or(c.valid_to IS NULL),"
        " count(DISTINCT c.para_id),"
        " (SELECT array_agg(DISTINCT e ORDER BY e) FROM chunks c2, unnest(c2.entity_codes) e"
        "  WHERE c2.doc_id = d.doc_id AND c2.trusted),"
        f" d.source_url, {score}"
        " FROM documents d JOIN chunks c ON c.doc_id = d.doc_id"
        " WHERE c.trusted AND c.entity_codes && %(entity)s::text[]"
        " AND c.valid_from <= %(as_of)s AND (c.valid_to IS NULL OR c.valid_to > %(as_of)s)"
        f" GROUP BY d.doc_id{having}"
    )
    out = [
        DirectionRecord(
            doc_id=r[0],
            title=r[1],
            instrument_type=r[2],
            authority_rank=r[3],
            binding=_binding(r[3]),
            in_force_from=r[4],
            in_force_to=None if r[6] else r[5],
            paragraphs_in_force=r[7],
            entity_scope=list(r[8] or []),
            source_url=r[9],
            topic_score=None if r[10] is None else round(float(r[10]), 4),
        )
        for r in conn.execute(sql, params).fetchall()
    ]
    if topic is None:
        return sorted(out, key=lambda d: (d.title, d.doc_id))
    return sorted(out, key=lambda d: (-(d.topic_score or 0.0), d.title, d.doc_id))


# --------------------------------------------------------------------------- a circular


class Withdrawal(_Model):
    withdrawn_by: str
    effective_on: date
    title: str | None
    circular_date: date | None


ResolutionStatus = Literal["withdrawn", "withdrawn_later", "consolidated", "mentioned", "not_found"]
DiffStatus = Literal["changed", "unchanged", "not_covered"]


class Resolution(_Model):
    reference: str  # as given
    canonical_ref: str
    as_of: date
    status: ResolutionStatus
    message: str
    withdrawals: list[Withdrawal]
    consolidated_into: list[str]  # doc_ids
    mentions: list[ParagraphRecord]  # in-force paragraphs that cite it


class _NeverEmbed:
    """Resolution is a lookup: an embedding call here is a bug, so it raises."""

    identity = ModelIdentity("none/lookup-only", 256, "float16")

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        raise AssertionError("resolving a circular reference made an embedding call")


def resolve_reference(
    conn: Any, reference: str, as_of: date, entity_codes: Sequence[str]
) -> Resolution:
    """What a circular reference resolves to on a date: RBI's withdrawal of it, the direction
    that consolidated it (both hold for every entity), and the paragraphs in force on that
    date for one of ``entity_codes`` that cite it. No model, no embedding."""
    found = find_references(reference)
    if not found:
        raise ValueError(f"no circular reference recognised in {reference!r}")
    surface, canonical = found[0]
    result = retrieve(
        conn,
        surface,
        entity_codes,
        as_of,
        embedder=_NeverEmbed(),
        options=RetrievalOptions(top_n=10, expand=False),
    )
    match = next((m for m in result.references if m.canonical_ref == canonical), None)
    withdrawals = [
        Withdrawal(
            withdrawn_by=w["withdrawn_by_doc_id"],
            effective_on=w["effective_on"],
            title=w["title"],
            circular_date=w["circular_date"],
        )
        for w in (match.withdrawals if match else [])
    ]
    into = list(match.consolidated_into) if match else []
    mentions = [m for c in result.candidates if (m := fetch_paragraph(conn, c.para_id))]
    effective = [w for w in withdrawals if w.effective_on <= as_of]
    status: ResolutionStatus
    if effective:
        w = min(effective, key=lambda w: w.effective_on)
        status = "withdrawn"
        message = (
            f"{surface} was withdrawn by {w.withdrawn_by} with effect from"
            f" {w.effective_on.isoformat()}, on or before {as_of.isoformat()}."
        )
    elif withdrawals:
        w = min(withdrawals, key=lambda w: w.effective_on)
        status = "withdrawn_later"
        message = (
            f"{surface} was withdrawn by {w.withdrawn_by} with effect from"
            f" {w.effective_on.isoformat()}, after {as_of.isoformat()}, so it may still have"
            " applied on that date. Its text is not in the index."
        )
    elif into:
        status = "consolidated"
        message = f"{surface} was consolidated into {', '.join(into)}."
    elif mentions:
        status = "mentioned"
        message = (
            f"{surface} is cited by {len(mentions)} paragraph(s) in force on that date for the"
            " entity asked about."
        )
    else:
        status = "not_found"
        message = (
            f"Nothing in the index withdraws, consolidates or cites {surface}"
            f" as of {as_of.isoformat()}."
        )
    if into and status != "consolidated":
        message += f" It was consolidated into {', '.join(into)}."
    return Resolution(
        reference=reference,
        canonical_ref=canonical,
        as_of=as_of,
        status=status,
        message=message,
        withdrawals=withdrawals,
        consolidated_into=into,
        mentions=mentions,
    )


# --------------------------------------------------------------------------- version history


class Version(_Model):
    doc_id: str
    version_seq: int
    in_force_from: date
    in_force_to: date | None


class MissingVersion(_Model):
    version_seq: int
    after: str | None  # the version before it that the index holds
    before: str | None  # the version after it that the index holds
    note: str


class VersionHistory(_Model):
    direction: str  # the doc_id without its version
    versions: list[Version]  # oldest first
    missing: list[MissingVersion]
    covered_from: date
    covered_to: date | None  # None: open ended, the latest version is in force


def version_history(conn: Any, doc_id: str) -> VersionHistory | None:
    direction = doc_id.split("#", 1)[0].rsplit(":v", 1)[0]
    rows = conn.execute(
        "SELECT d.doc_id, d.version_seq, min(c.valid_from), max(c.valid_to),"
        " bool_or(c.valid_to IS NULL)"
        " FROM documents d JOIN chunks c ON c.doc_id = d.doc_id AND c.trusted"
        " WHERE d.doc_id LIKE %s GROUP BY d.doc_id ORDER BY d.version_seq",
        (direction + ":v%",),
    ).fetchall()
    if not rows:
        return None
    versions = [
        Version(
            doc_id=r[0], version_seq=r[1], in_force_from=r[2], in_force_to=None if r[4] else r[3]
        )
        for r in rows
    ]
    have = {v.version_seq for v in versions}
    missing = []
    for seq in sorted(set(range(1, max(have) + 1)) - have):
        earlier = [v for v in versions if v.version_seq < seq]
        later = [v for v in versions if v.version_seq > seq]
        missing.append(
            MissingVersion(
                version_seq=seq,
                after=earlier[-1].doc_id if earlier else None,
                before=later[0].doc_id if later else None,
                note="RBI published this version only as a PDF; its text and its dates are not"
                " in the index",
            )
        )
    return VersionHistory(
        direction=direction,
        versions=versions,
        missing=missing,
        covered_from=versions[0].in_force_from,
        covered_to=versions[-1].in_force_to,
    )


class Change(_Model):
    anchor: str
    kind: Literal["added", "removed", "changed"]
    from_para_id: str | None
    to_para_id: str | None
    from_body: str | None
    to_body: str | None


class VersionDiff(_Model):
    direction: str
    from_date: date
    to_date: date
    from_version: str | None
    to_version: str | None
    status: DiffStatus
    message: str
    changes: list[Change]
    unchanged_paragraphs: int
    history: VersionHistory


def _version_on(history: VersionHistory, day: date) -> Version | None:
    for v in history.versions:
        if v.in_force_from <= day and (v.in_force_to is None or v.in_force_to > day):
            return v
    return None


def _paragraphs(conn: Any, doc_id: str) -> dict[str, tuple[str, str]]:
    """anchor -> (para_id, body), live paragraphs only: a deletion marker is no paragraph."""
    rows = conn.execute(
        "SELECT para_id, body FROM paragraphs WHERE doc_id = %s ORDER BY ordinal", (doc_id,)
    ).fetchall()
    return {pid.split("#", 1)[1]: (pid, body) for pid, body in rows if not _DELETED.match(body)}


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def diff_versions(conn: Any, doc_id: str, from_date: date, to_date: date) -> VersionDiff | None:
    """The paragraph level change between the versions of a direction in force on two dates,
    as far as the index can see. None if the index holds no such direction."""
    if from_date > to_date:
        raise ValueError("from_date is after to_date")
    history = version_history(conn, doc_id)
    if history is None:
        return None
    a, b = _version_on(history, from_date), _version_on(history, to_date)

    def result(status: DiffStatus, message: str, changes: list[Change], same: int) -> VersionDiff:
        return VersionDiff(
            direction=history.direction,
            from_date=from_date,
            to_date=to_date,
            from_version=a.doc_id if a else None,
            to_version=b.doc_id if b else None,
            status=status,
            message=message,
            changes=changes,
            unchanged_paragraphs=same,
            history=history,
        )

    if a is None or b is None:
        end = f" to {history.covered_to.isoformat()}" if history.covered_to else " onwards"
        gone = " or ".join(d.isoformat() for d, v in ((from_date, a), (to_date, b)) if v is None)
        earlier = [str(m.version_seq) for m in history.missing if m.after is None]
        pdf = (
            f" Version(s) {', '.join(earlier)}, before the first the index holds, were published"
            " by RBI only as a PDF."
            if earlier
            else ""
        )
        return result(
            "not_covered",
            f"The index holds versions of {history.direction} in force from"
            f" {history.covered_from.isoformat()}{end}, and none in force on {gone}.{pdf} No"
            " change set is given, rather than an empty one that would read as no change.",
            [],
            0,
        )
    ends = (a.doc_id, b.doc_id)
    gaps = [
        m
        for m in history.missing
        if m.after in ends or a.version_seq < m.version_seq < b.version_seq
    ]
    gap_note = (
        " Version(s) "
        + ", ".join(str(m.version_seq) for m in gaps)
        + " of this direction, published by RBI only as a PDF, fall in or next to this span; a"
        " change they made, or a date they governed, is not visible here."
        if gaps
        else ""
    )
    if a.doc_id == b.doc_id:
        return result(
            "unchanged",
            f"The same version, {a.doc_id}, was in force on both dates, so the index holds no"
            f" change between them.{gap_note}",
            [],
            len(_paragraphs(conn, a.doc_id)),
        )
    old, new = _paragraphs(conn, a.doc_id), _paragraphs(conn, b.doc_id)
    changes: list[Change] = []
    same = 0
    none: tuple[None, None] = (None, None)
    for anchor in dict.fromkeys([*old, *new]):
        o, n = old.get(anchor), new.get(anchor)
        if o is not None and n is not None and _same(o[1], n[1]):
            same += 1
            continue
        kind: Literal["added", "removed", "changed"] = (
            "added" if o is None else "removed" if n is None else "changed"
        )
        (op, ob), (np_, nb) = o or none, n or none
        changes.append(
            Change(
                anchor=anchor,
                kind=kind,
                from_para_id=op,
                to_para_id=np_,
                from_body=ob,
                to_body=nb,
            )
        )
    between = [v.doc_id for v in history.versions if a.version_seq < v.version_seq < b.version_seq]
    via = f" The change is net of {', '.join(between)} in between." if between else ""
    return result(
        "changed" if changes else "unchanged",
        f"{a.doc_id} (in force on {from_date.isoformat()}) against {b.doc_id} (in force on"
        f" {to_date.isoformat()}): {len(changes)} paragraphs differ, {same} are unchanged."
        f"{via}{gap_note}",
        changes,
        same,
    )


# --------------------------------------------------------------------------- the build


class EntityTypeRecord(_Model):
    code: str
    label: str
    parent: str | None
    children: list[str]


def entity_taxonomy(conn: Any) -> list[EntityTypeRecord]:
    """The build's entity types, with the one hierarchy the corpus defines: NBFC subtypes
    (``nbfc_*``) under ``nbfc`` (see ``extant.corpora.rbi.entities``)."""
    found = conn.execute("SELECT code, label FROM entity_types ORDER BY code").fetchall()
    rows = [(str(c), str(label)) for c, label in found]
    codes = {c for c, _ in rows}

    def parent(code: str) -> str | None:
        return "nbfc" if code.startswith("nbfc_") and "nbfc" in codes else None

    return [
        EntityTypeRecord(
            code=c,
            label=label,
            parent=parent(c),
            children=sorted(x for x in codes if parent(x) == c),
        )
        for c, label in rows
    ]


def manifest(conn: Any, schema: str) -> dict[str, Any]:
    """The build's own manifest, and the coverage a client needs to say what it is querying."""
    rows = dict(conn.execute("SELECT key, value FROM build_manifest").fetchall())
    docs = conn.execute(
        "SELECT count(*), count(DISTINCT split_part(doc_id, ':v', 1)),"
        " count(*) FILTER (WHERE version_seq > 1) FROM documents"
    ).fetchone()
    validity = conn.execute("SELECT min(valid_from), max(valid_to) FROM chunks").fetchone()
    gaps = conn.execute(
        "SELECT count(*) FROM (SELECT split_part(doc_id, ':v', 1), max(version_seq) m, count(*) n"
        " FROM documents GROUP BY 1) x WHERE m <> n"
    ).fetchone()[0]
    return {
        "index_version": schema,
        # Everything the build recorded except the per-document source hashes, 630 of them.
        "build": {k: v for k, v in rows.items() if k != "source_sha256"},
        "coverage": {
            "documents": docs[0],
            "directions": docs[1],
            "later_versions": docs[2],
            "directions_missing_a_version": gaps,
            "earliest_in_force": validity[0].isoformat() if validity[0] else None,
            "latest_validity_change": validity[1].isoformat() if validity[1] else None,
            "notes": [
                "RBI's consolidated Master Directions and Directions, version by version, from"
                " the November 2025 consolidation onwards. Circulars RBI withdrew are known"
                " through its withdrawal lists and consolidation annexes, not as text.",
                "A version RBI published only as a PDF is not in the index; diff_versions"
                " says where that leaves a gap.",
                "No FAQ or press release is in the index: every paragraph is binding.",
            ],
        },
    }
