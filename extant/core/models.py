"""Domain models.

One model per table in the index build schema, plus ``Citation``, which has no table and
is the only thing the composer is allowed to emit. The models are the boundary: a row that
would violate an identity grammar, a hash format or an interval rule fails here, at
construction, not three phases later.

Two text columns, and the rule that governs them
------------------------------------------------
A chunk stores both ``body`` and ``embed_text``. ``embed_text`` is what gets embedded: the
document title, heading path and entity scope composed in front of the paragraph (see
``extant.core.embedding``). ``body`` is the paragraph as published. Groundedness checks and
quote verification always run against ``body``, never ``embed_text``, otherwise the composed
header could be quoted back as if it were regulation. The lexical index (``chunks.fts``) is
generated from ``body`` for the same reason.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from extant.core.authority import AuthorityRank
from extant.core.constants import EMBEDDING_DIM, HALFVEC_MAX, INTEGER_MAX, SMALLINT_MAX
from extant.core.hashing import sha256_hex
from extant.core.identity import (
    CORPUS_RE,
    InvalidIdentifierError,
    canonical_ref,
    doc_id_of,
    parse_doc_id,
    parse_para_id,
    parse_schema_name,
    validate_entity_code,
    validate_source_anchor,
    withdrawal_key,
)

# --------------------------------------------------------------------------- field types


def _doc_id(value: str) -> str:
    parse_doc_id(value)
    return value


def _para_id(value: str) -> str:
    parse_para_id(value)
    return value


def _schema_name(value: str) -> str:
    parse_schema_name(value)
    return value


def _corpus(value: str) -> str:
    if CORPUS_RE.fullmatch(value) is None:
        raise InvalidIdentifierError(f"malformed corpus {value!r}, expected e.g. 'rbi'")
    return value


def _not_blank(value: str) -> str:
    # Checked, never stripped: bodies and quotes must stay byte for byte as published.
    if not value.strip():
        raise ValueError("must not be empty or whitespace only")
    return value


DocIdStr = Annotated[str, AfterValidator(_doc_id)]
ParaIdStr = Annotated[str, AfterValidator(_para_id)]
SchemaNameStr = Annotated[str, AfterValidator(_schema_name)]
CorpusStr = Annotated[str, AfterValidator(_corpus)]
EntityCode = Annotated[str, AfterValidator(validate_entity_code)]
NonBlankStr = Annotated[str, AfterValidator(_not_blank)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
HttpUrlStr = Annotated[str, StringConstraints(pattern=r"^https?://\S+$")]
HalfFloat = Annotated[float, Field(allow_inf_nan=False, ge=-HALFVEC_MAX, le=HALFVEC_MAX)]
PageNumber = Annotated[int, Field(ge=1, le=INTEGER_MAX)]
# '#<element id>' or 'block:<index>' into the extracted source; see identity.py.
SourceAnchor = Annotated[str, AfterValidator(validate_source_anchor)]

FacetScope = Literal["document", "paragraph"]
ConcordanceRelation = Literal["consolidated_into", "repealed", "obsolete", "retained"]


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- tables


class Document(_Row):
    """One version of one instrument. An amendment is a new Document with the next
    version and a ``supersedes_doc_id`` pointer; nothing is edited in place.

    ``effective_to >= effective_from`` mirrors the table's CHECK constraint exactly.
    """

    doc_id: DocIdStr
    corpus: CorpusStr
    title: NonBlankStr
    instrument_type: NonBlankStr
    authority_rank: AuthorityRank
    issued_on: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    supersedes_doc_id: DocIdStr | None = None
    version_seq: int = Field(default=1, ge=1, le=SMALLINT_MAX)
    source_url: HttpUrlStr
    source_sha256: Sha256Hex
    parse_status: NonBlankStr

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        parts = parse_doc_id(self.doc_id)
        if parts.corpus != self.corpus:
            raise ValueError(f"corpus {self.corpus!r} disagrees with doc_id {self.doc_id!r}")
        if parts.version != self.version_seq:
            raise ValueError(
                f"version_seq {self.version_seq} disagrees with doc_id {self.doc_id!r}"
            )
        if self.version_seq > 1 and self.supersedes_doc_id is None:
            raise ValueError("a document after v1 must name the document it supersedes")
        if self.supersedes_doc_id == self.doc_id:
            raise ValueError("a document cannot supersede itself")
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to is before effective_from")
        return self


class Paragraph(_Row):
    """A paragraph as published. ``body`` is verbatim, and ``content_hash`` is
    ``sha256(body)`` over its UTF-8 bytes.

    Located by ``source_page`` (a PDF source), ``source_anchor`` (an HTML source, resolved in
    the page whose bytes hash to the document's ``source_sha256``), or both; never neither.
    """

    para_id: ParaIdStr
    doc_id: DocIdStr
    ordinal: int = Field(ge=0, le=INTEGER_MAX)
    chapter: str | None = None
    para_number: str | None = None
    heading_path: str | None = None  # 'Chapter IV > Reporting > 4.3A'
    body: NonBlankStr
    content_hash: Sha256Hex
    source_page: PageNumber | None = None
    source_anchor: SourceAnchor | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if doc_id_of(self.para_id) != self.doc_id:
            raise ValueError(f"para_id {self.para_id!r} does not belong to {self.doc_id!r}")
        if self.content_hash != sha256_hex(self.body):
            raise ValueError("content_hash is not sha256(body)")
        if self.source_page is None and self.source_anchor is None:
            raise ValueError("a paragraph needs a source_page, a source_anchor, or both")
        return self


class Facet(_Row):
    """A conditional attribute off the hot path, attached to a document or a paragraph."""

    scope: FacetScope
    ref_id: str
    key: NonBlankStr
    value: NonBlankStr

    @model_validator(mode="after")
    def _ref_matches_scope(self) -> Self:
        if self.scope == "document":
            parse_doc_id(self.ref_id)
        else:
            parse_para_id(self.ref_id)
        return self


class EntityType(_Row):
    code: EntityCode  # nbfc_ul, sfb, ucb, pb, arc, cic, aifi ...
    label: NonBlankStr


class Chunk(_Row):
    """A row of the hot table: filters, lexical index and vector together.

    Validity is a half-open interval ``[valid_from, valid_to)``. A chunk is in force on
    ``asof`` exactly when::

        valid_from <= asof AND (valid_to IS NULL OR valid_to > asof)

    so ``valid_to`` is the first day the chunk no longer applies, and an open ``valid_to``
    means in force until further notice. Retrieval SQL must use this predicate verbatim;
    ``valid_at`` is its Python twin.

    ``body`` is the paragraph text as published and is what quotes and groundedness are
    checked against. ``embed_text`` is the composed string that gets embedded, and must
    never be quoted. ``content_hash`` is ``sha256(embed_text)``: it keys
    ``public.vec_cache``, and the cached vector is a function of the embedded string, so a
    changed title, heading or entity scope over an unchanged body must miss the cache.

    ``chunk_id`` is assigned by the database (bigserial) and is None before insert. The
    generated ``fts`` column is database-only and has no field here.
    """

    chunk_id: int | None = Field(default=None, ge=1)
    para_id: ParaIdStr
    doc_id: DocIdStr
    body: NonBlankStr
    embed_text: NonBlankStr
    content_hash: Sha256Hex
    entity_codes: tuple[EntityCode, ...] = Field(min_length=1)
    valid_from: date
    valid_to: date | None = None
    authority_rank: AuthorityRank
    embedding: tuple[HalfFloat, ...] = Field(min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if doc_id_of(self.para_id) != self.doc_id:
            raise ValueError(f"para_id {self.para_id!r} does not belong to {self.doc_id!r}")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be strictly after valid_from (half-open interval)")
        if len(set(self.entity_codes)) != len(self.entity_codes):
            raise ValueError("entity_codes contains duplicates")
        if self.content_hash != sha256_hex(self.embed_text):
            raise ValueError("content_hash is not sha256(embed_text)")
        return self

    def valid_at(self, asof: date) -> bool:
        return self.valid_from <= asof and (self.valid_to is None or self.valid_to > asof)


class ConcordanceEntry(_Row):
    """Gold label set 1, mined from the consolidation annexes. ``old_ref`` is the
    canonical form produced by ``identity.canonical_ref``."""

    old_ref: NonBlankStr
    old_title: str | None = None
    old_date: date | None = None
    new_doc_id: DocIdStr | None = None
    new_para_id: ParaIdStr | None = None
    relation: ConcordanceRelation
    evidence_doc_id: DocIdStr
    evidence_page: PageNumber | None = None
    evidence_anchor: SourceAnchor | None = None  # for evidence read from HTML

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (
            self.new_doc_id is not None
            and self.new_para_id is not None
            and doc_id_of(self.new_para_id) != self.new_doc_id
        ):
            raise ValueError(f"new_para_id {self.new_para_id!r} is not in {self.new_doc_id!r}")
        return self


class Withdrawal(_Row):
    """Gold label set 2, the published withdrawal list.

    ``announced_on`` and ``effective_on`` are separate because some circulars carry a
    deferred repeal date later than the announcement, and an as-of date in between must
    still find the circular in force. ``effective_on`` is the first day it no longer
    applies, matching the half-open convention on ``Chunk``.
    """

    withdrawal_key: Sha256Hex  # identity.withdrawal_key(canonical_ref, circular_date, title)
    circular_ref: NonBlankStr  # as published, which may not be a reference at all
    canonical_ref: str | None = None  # the join key; None when circular_ref is not a reference
    title: str | None = None
    circular_date: date
    withdrawn_by_doc_id: DocIdStr
    announced_on: date
    effective_on: date

    @model_validator(mode="after")
    def _keyed(self) -> Self:
        if (
            self.canonical_ref is not None
            and canonical_ref(self.canonical_ref) != self.canonical_ref
        ):
            raise ValueError(f"canonical_ref {self.canonical_ref!r} is not in canonical form")
        expected = withdrawal_key(self.canonical_ref, self.circular_date, self.title)
        if self.withdrawal_key != expected:
            raise ValueError("withdrawal_key is not the key of canonical_ref, circular_date, title")
        return self


class ParaIdAlias(_Row):
    """Parser drift. A paragraph id retired by a reparse resolves through here.
    ``new_para_id`` is None when the paragraph has no successor."""

    old_para_id: ParaIdStr
    new_para_id: ParaIdStr | None = None
    retired_in: SchemaNameStr  # the index schema that retired it
    reason: NonBlankStr

    @model_validator(mode="after")
    def _not_self(self) -> Self:
        if self.old_para_id == self.new_para_id:
            raise ValueError("a paragraph id cannot alias itself")
        return self


class BuildManifest(_Row):
    key: NonBlankStr
    value: JsonValue


# --------------------------------------------------------------------------- agent level


class CitationError(ValueError):
    """A citation does not hold against the paragraph it names."""


class Citation(_Row):
    """What the composer is allowed to emit: a paragraph, a verbatim quote from it and where
    it sits in the source, a page, a source anchor, or both. There is no table behind it.

    ``verify`` checks the quote against ``Paragraph.body`` and nothing else. Checking
    against ``embed_text`` would let the composed header be cited as regulation. It also
    refuses a source anchor that differs from the paragraph's own. A page is not compared,
    because a paragraph can run across pages.
    """

    para_id: ParaIdStr
    quote: NonBlankStr
    source_page: PageNumber | None = None
    source_anchor: SourceAnchor | None = None

    @model_validator(mode="after")
    def _located(self) -> Self:
        if self.source_page is None and self.source_anchor is None:
            raise ValueError("a citation needs a source_page, a source_anchor, or both")
        return self

    @property
    def doc_id(self) -> str:
        return doc_id_of(self.para_id)

    def verify(self, paragraph: Paragraph) -> None:
        """Raise CitationError unless the quote is a verbatim substring of the body of
        the paragraph this citation names."""
        if paragraph.para_id != self.para_id:
            raise CitationError(
                f"citation names {self.para_id!r} but was checked against {paragraph.para_id!r}"
            )
        if self.quote not in paragraph.body:
            raise CitationError(f"quote is not a verbatim substring of {self.para_id!r}")
        if (
            self.source_anchor is not None
            and paragraph.source_anchor is not None
            and self.source_anchor != paragraph.source_anchor
        ):
            raise CitationError(
                f"citation points to {self.source_anchor!r} but {self.para_id!r} is at"
                f" {paragraph.source_anchor!r}"
            )
