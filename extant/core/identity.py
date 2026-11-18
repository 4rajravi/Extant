"""Durable identifiers.

A citation is only as durable as the identifier inside it, so the grammars in this module
are the part of Extant that must not change after Phase 0. Every identifier has exactly
one canonical spelling: the constructors produce it, the parsers accept nothing else, and
``make_*(parse_*(x)) == x`` holds for every valid ``x``.

    doc_id   <corpus>:<instrument>:<year>:<serial>:v<version>    rbi:md:2025:0147:v1
    para_id  <doc_id>#<anchor>                                   rbi:md:2025:0147:v1#ch4.3A
    schema   idx_<corpus>_<YYYYMMDD>_<short git commit>          idx_rbi_20261118_a3f91c4

Grammar, token by token.

corpus      lowercase letter then 1 to 15 lowercase letters or digits (``rbi``, ``itact``,
            ``gst``). No underscores, so a schema name splits unambiguously.
instrument  lowercase letter then up to 15 lowercase letters or digits (``md``, ``circ``,
            ``faq``). A corpus with several numbering series per year (GST notifications
            under Central Tax and Central Tax (Rate), for example) gives each series its
            own instrument token so serials never collide.
year        four digits, 1000 to 2999.
serial      zero padded to four digits; a serial of 10000 or more is written without
            padding. ``0147`` is canonical, ``147`` and ``00147`` are rejected.
version     ``v`` then a positive integer with no leading zero, at most 32767 because
            ``documents.version_seq`` is a smallint. An amendment creates ``v2`` as new
            rows with a ``supersedes_doc_id`` pointer; nothing is edited in place.
anchor      a lowercase kind prefix then dot separated alphanumeric segments, for example
            ``ch4.3A``, ``annex2.1``, ``s80C.2.xviii``. Parentheses and spaces from the
            printed numbering are mapped to dots by the corpus parser. The grammar fixes
            the character set and shape; what each segment means is the corpus adapter's
            business, so a second corpus needs no change here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from extant.core.constants import SMALLINT_MAX


class InvalidIdentifierError(ValueError):
    """An identifier does not match its grammar. Subclasses ValueError so Pydantic
    validators surface it as a normal validation error."""


# --------------------------------------------------------------------------- tokens

_CORPUS = r"[a-z][a-z0-9]{1,15}"
_INSTRUMENT = r"[a-z][a-z0-9]{0,15}"
_YEAR = r"[12][0-9]{3}"
_SERIAL = r"[0-9]{4}|[1-9][0-9]{4,7}"
_VERSION = r"[1-9][0-9]{0,4}"
_ANCHOR = r"[a-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)*"
_COMMIT = r"[0-9a-f]{7,12}"
_ENTITY_CODE = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*"

_DOC_ID_BODY = rf"{_CORPUS}:{_INSTRUMENT}:{_YEAR}:(?:{_SERIAL}):v{_VERSION}"

MAX_ANCHOR_LENGTH = 120
POSTGRES_MAX_IDENTIFIER = 63

# All patterns are used with fullmatch, never match or search, so a trailing newline or
# any other surrounding text is rejected.
CORPUS_RE = re.compile(_CORPUS)
ENTITY_CODE_RE = re.compile(_ENTITY_CODE)
ANCHOR_RE = re.compile(_ANCHOR)
DOC_ID_RE = re.compile(
    rf"(?P<corpus>{_CORPUS}):(?P<instrument>{_INSTRUMENT}):(?P<year>{_YEAR})"
    rf":(?P<serial>{_SERIAL}):v(?P<version>{_VERSION})"
)
PARA_ID_RE = re.compile(rf"(?P<doc_id>{_DOC_ID_BODY})#(?P<anchor>{_ANCHOR})")
SCHEMA_NAME_RE = re.compile(
    rf"idx_(?P<corpus>{_CORPUS})_(?P<build_date>[0-9]{{8}})_(?P<commit>{_COMMIT})"
)


# --------------------------------------------------------------------------- doc_id


@dataclass(frozen=True, slots=True)
class DocId:
    corpus: str
    instrument: str
    year: int
    serial: int
    version: int

    def __str__(self) -> str:
        return make_doc_id(self.corpus, self.instrument, self.year, self.serial, self.version)

    @property
    def base(self) -> str:
        """The identifier without its version, shared by every version of one instrument."""
        return str(self).rsplit(":", 1)[0]


def make_doc_id(corpus: str, instrument: str, year: int, serial: int, version: int) -> str:
    """Build the canonical doc_id. Raises InvalidIdentifierError on any bad component."""
    if not 1 <= version <= SMALLINT_MAX:
        raise InvalidIdentifierError(f"version must be between 1 and {SMALLINT_MAX}: {version}")
    if serial < 0:
        raise InvalidIdentifierError(f"serial must be non-negative: {serial}")
    candidate = f"{corpus}:{instrument}:{year:04d}:{serial:04d}:v{version}"
    parse_doc_id(candidate)
    return candidate


def parse_doc_id(value: str) -> DocId:
    m = DOC_ID_RE.fullmatch(value)
    if m is None:
        raise InvalidIdentifierError(
            f"malformed doc_id {value!r}, expected <corpus>:<instrument>:<year>:<serial>:v<n>"
            " such as 'rbi:md:2025:0147:v1'"
        )
    version = int(m["version"])
    if version > SMALLINT_MAX:
        raise InvalidIdentifierError(f"doc_id version exceeds {SMALLINT_MAX}: {value!r}")
    return DocId(
        corpus=m["corpus"],
        instrument=m["instrument"],
        year=int(m["year"]),
        serial=int(m["serial"]),
        version=version,
    )


# --------------------------------------------------------------------------- para_id


@dataclass(frozen=True, slots=True)
class ParaId:
    doc: DocId
    anchor: str

    @property
    def doc_id(self) -> str:
        return str(self.doc)

    def __str__(self) -> str:
        return make_para_id(self.doc_id, self.anchor)


def make_para_id(doc_id: str, anchor: str) -> str:
    """Build the canonical para_id from a valid doc_id and an anchor."""
    parse_doc_id(doc_id)
    if len(anchor) > MAX_ANCHOR_LENGTH or ANCHOR_RE.fullmatch(anchor) is None:
        raise InvalidIdentifierError(
            f"malformed anchor {anchor!r}, expected a lowercase kind prefix and dot separated"
            f" alphanumeric segments such as 'ch4.3A', at most {MAX_ANCHOR_LENGTH} characters"
        )
    return f"{doc_id}#{anchor}"


def parse_para_id(value: str) -> ParaId:
    m = PARA_ID_RE.fullmatch(value)
    if m is None or len(m["anchor"]) > MAX_ANCHOR_LENGTH:
        raise InvalidIdentifierError(
            f"malformed para_id {value!r}, expected <doc_id>#<anchor>"
            " such as 'rbi:md:2025:0147:v1#ch4.3A'"
        )
    return ParaId(doc=parse_doc_id(m["doc_id"]), anchor=m["anchor"])


def doc_id_of(para_id: str) -> str:
    """The doc_id a para_id belongs to."""
    return parse_para_id(para_id).doc_id


# --------------------------------------------------------------------------- index schema names


@dataclass(frozen=True, slots=True)
class SchemaName:
    corpus: str
    build_date: date
    commit: str

    def __str__(self) -> str:
        return make_schema_name(self.corpus, self.build_date, self.commit)


def make_schema_name(corpus: str, build_date: date, commit: str) -> str:
    candidate = f"idx_{corpus}_{build_date:%Y%m%d}_{commit}"
    parse_schema_name(candidate)
    return candidate


def parse_schema_name(value: str) -> SchemaName:
    """Validate an index build schema name.

    This is the guard that stands between a typo and ``DROP SCHEMA public``. It accepts
    only ``idx_<corpus>_<YYYYMMDD>_<7 to 12 lowercase hex>`` with a real calendar date,
    and so rejects ``public``, the empty string and anything carrying quotes, semicolons
    or whitespace.
    """
    m = SCHEMA_NAME_RE.fullmatch(value) if len(value) <= POSTGRES_MAX_IDENTIFIER else None
    if m is None:
        raise InvalidIdentifierError(
            f"not an index schema name: {value!r}, expected idx_<corpus>_<YYYYMMDD>_<commit>"
            " such as 'idx_rbi_20261118_a3f91c4'"
        )
    try:
        build_date = datetime.strptime(m["build_date"], "%Y%m%d").date()
    except ValueError:
        raise InvalidIdentifierError(
            f"index schema name {value!r} carries an impossible date"
        ) from None
    return SchemaName(corpus=m["corpus"], build_date=build_date, commit=m["commit"])


def is_schema_name(value: str) -> bool:
    try:
        parse_schema_name(value)
    except InvalidIdentifierError:
        return False
    return True


# --------------------------------------------------------------------------- entity codes


def validate_entity_code(value: str) -> str:
    """Entity codes are lowercase snake case: ``nbfc_ul``, ``sfb``, ``ucb``."""
    if ENTITY_CODE_RE.fullmatch(value) is None:
        raise InvalidIdentifierError(f"malformed entity code {value!r}, expected e.g. 'nbfc_ul'")
    return value


# --------------------------------------------------------------------------- circular references


def canonical_ref(raw: str) -> str:
    """Normalise a circular reference to its canonical join key. Phase 1.

    Circular references reach us in many surface forms, from the consolidation annexes,
    the withdrawal list and the circulars themselves, and the concordance and withdrawal
    joins only work if every form collapses to one key. The canonical form is uppercase,
    has no spaces and has its departmental prefix normalised:

        'DBOD.No.BP.BC.1/21.04.048/2015-16'  ->  'DBOD.BP.BC.1/21.04.048/2015-16'

    Must be idempotent: ``canonical_ref(canonical_ref(x)) == canonical_ref(x)``.
    """
    raise NotImplementedError("canonical_ref is Phase 1, mined from the RBI corpus")


def ref_variants(canonical: str) -> list[str]:
    """Surface forms a canonical reference is known to appear as. Phase 1.

    The inverse direction of ``canonical_ref``, used to search raw text for mentions of a
    circular. Every variant must satisfy ``canonical_ref(v) == canonical``, and the list
    includes ``canonical`` itself.
    """
    raise NotImplementedError("ref_variants is Phase 1, mined from the RBI corpus")
