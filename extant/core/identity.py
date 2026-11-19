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

import hashlib
import re
import unicodedata
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


def make_doc_base(corpus: str, instrument: str, year: int, serial: int) -> str:
    """A doc_id without its version, ``rbi:md:2025:0204``: shared by every version of one
    instrument. Validated exactly as a full doc_id would be."""
    return make_doc_id(corpus, instrument, year, serial, 1).rsplit(":", 1)[0]


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


# --------------------------------------------------------------------------- source anchors

# Where in its source a paragraph sits, when the source has no page numbers. The RBI corpus is
# read from HTML (decided 19 November 2026), so a paragraph is located by an anchor into the
# extracted page, and the page itself is pinned by ``documents.source_sha256``:
#
#     #<element id>     the page's own id on the block, e.g. "#F9", "#C3K"; preferred, because
#                       the page links to it and a browser can jump to it
#     block:<index>     the block's position in the extracted page, from 0, when it has no id
#
# A paragraph from a PDF carries ``source_page`` instead, and may carry both.

_ELEMENT_ID = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"
SOURCE_ANCHOR_RE = re.compile(rf"#{_ELEMENT_ID}|block:(?:0|[1-9][0-9]{{0,6}})")


def make_id_anchor(element_id: str) -> str:
    return validate_source_anchor(f"#{element_id}")


def make_block_anchor(index: int) -> str:
    if index < 0:
        raise InvalidIdentifierError(f"block index must be non-negative: {index}")
    return validate_source_anchor(f"block:{index}")


def validate_source_anchor(value: str) -> str:
    if SOURCE_ANCHOR_RE.fullmatch(value) is None:
        raise InvalidIdentifierError(
            f"malformed source anchor {value!r}, expected '#<element id>' such as '#F9'"
            " or 'block:<index>' such as 'block:212'"
        )
    return value


# --------------------------------------------------------------------------- entity codes


def validate_entity_code(value: str) -> str:
    """Entity codes are lowercase snake case: ``nbfc_ul``, ``sfb``, ``ucb``."""
    if ENTITY_CODE_RE.fullmatch(value) is None:
        raise InvalidIdentifierError(f"malformed entity code {value!r}, expected e.g. 'nbfc_ul'")
    return value


# --------------------------------------------------------------------------- circular references
#
# The rules below were derived from the 10,323 distinct reference strings in RBI's three
# published lists (withdrawn, standalone, draft repeal) and the references inside the
# directions, observed in November 2026. Each rule names the surface forms it exists for.
# They are deliberately conservative: forms are merged only where the difference is plainly
# typographic. Leading zeros, renamed departments (DBOD, DBR, DOR) and guessed typos such as
# DBDO are left alone, because merging two real circulars would corrupt every join silently.


class InvalidReferenceError(InvalidIdentifierError):
    """A string in a reference column that is not a circular reference at all, such as
    "Mail Box Clarification" or "D.O. Letter"."""


# Descriptive words written in front of a reference: "Master Direction DBR.Dir.No.85/...",
# "Master Direction/DBR.FSD.No.101/...", "Notification No.DNBR.041/...", "RBI circular DBR...".
_INSTRUMENT_PREFIX = re.compile(
    r"^(?:MASTER\s+DIRECTIONS?|MASTER\s+CIRCULARS?|NOTIFICATIONS?|RBI\s+CIRCULARS?|CIRCULARS?)"
    r"(?=[\s/.:\-]|NO\b)[\s/.:\-]*"
)
# "RBI/2016-17/DBR.FID.No.1/01.02.000/2016-17": an RBI serial wrapped around a department
# reference. A bare RBI serial such as "RBI/DOR/2025-26/269" is the reference and is kept.
_RBI_WRAPPER = re.compile(r"^RBI/\d{4}-\d{2,4}/(?=[A-Z]{2,}[A-Z.()]*\.)")
# "DBODNo.", "RPCDNo.", "UBDNo.": a department code glued to "No.".
_GLUED_NO = re.compile(r"^(DBOD|RPCD|UBD)NO(?=[.:])")
# The "No." token in all its spellings: "No.", "NO.", "No:", "No. ", "No ", and "No" before a
# separator. Only a standalone token, never the letters inside a word.
_NO_TOKEN = re.compile(r"(?<![A-Z0-9])NO(?:[.:]\s*|\s+|(?=[(/]))")
# Typographic errors in a department code that are unambiguous: a lower case L for an I
# ("lECD.No.2046/04.02.02B/2001-02"); there is no LECD department.
_PREFIX_TYPOS = {"LECD": "IECD"}
_SPACE_BETWEEN_WORDS = re.compile(r"(?<=[A-Z0-9])\s+(?=[A-Z0-9])")
_TRAILING = re.compile(r"[\s.,;*@]+$")
_SURFACE_ENDS = re.compile(r"^[\s.,;:]+")


def canonical_ref(raw: str) -> str:
    """Normalise a circular reference to its canonical join key.

    Circular references reach us in many surface forms, from the published lists and the
    directions themselves, and the withdrawal and concordance joins only work if every form
    collapses to one key. The canonical form is uppercase, has no spaces, no "No." tokens and
    no descriptive prefix:

        'DBOD.No.BP.BC.1/21.04.048/2015-16'           ->  'DBOD.BP.BC.1/21.04.048/2015-16'
        'Notification No. DFC.55/DG (O)-87'           ->  'DFC.55/DG(O)-87'
        'DOR.SIG.FIN.REC 8/26.03.001/2023-24'         ->  'DOR.SIG.FIN.REC.8/26.03.001/2023-24'
        'RPCD.No.RRB.BC.35/03.05.33©/2008-09'    ->  'RPCD.RRB.BC.35/03.05.33(C)/2008-09'

    Idempotent: ``canonical_ref(canonical_ref(x)) == canonical_ref(x)``. Raises
    InvalidReferenceError for a string with no digit in it, which is not a reference.
    """
    s = unicodedata.normalize("NFKC", raw).replace("©", "(C)").replace("\\", "/")
    s = s.upper().strip()
    s = _TRAILING.sub("", _SURFACE_ENDS.sub("", s))
    s = _INSTRUMENT_PREFIX.sub("", s)
    s = _RBI_WRAPPER.sub("", s)
    s = _GLUED_NO.sub(r"\1.", s)
    s = _NO_TOKEN.sub(".", s)
    s = _SPACE_BETWEEN_WORDS.sub(".", s)
    s = re.sub(r"\s+", "", s).replace(":", ".")
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"\.(?=[/(),])|(?<=[/(,])\.", "", s)
    s = s.strip(".")
    head, sep, rest = s.partition(".")
    s = _PREFIX_TYPOS.get(head, head) + sep + rest
    if not any(ch.isdigit() for ch in s):
        raise InvalidReferenceError(f"not a circular reference: {raw!r}")
    return s


def ref_variants(canonical: str) -> list[str]:
    """Surface forms a canonical reference is known to appear as.

    The inverse direction of ``canonical_ref``, used to search raw text for mentions of a
    circular: the canonical form; "No." after the department code ("DBOD.No.BP.BC.1/..."); "No."
    before the serial number ("DBOD.BP.BC.No.1/..."); and a mixed case department for the
    codes RBI writes that way ("DoR.", "DoS."). Every variant satisfies
    ``canonical_ref(v) == canonical``, and the list starts with ``canonical`` itself.
    """
    if canonical_ref(canonical) != canonical:
        raise InvalidReferenceError(f"not a canonical reference: {canonical!r}")
    candidates = [canonical]
    head, sep, rest = canonical.partition(".")
    if sep:
        candidates.append(f"{head}.No.{rest}")
    serial = re.search(r"(?<=\.)(\d[^./]*)(?=/)", canonical)
    if serial:
        candidates.append(canonical[: serial.start()] + "No." + canonical[serial.start() :])
    for code, mixed in (("DOR", "DoR"), ("DOS", "DoS")):
        if canonical.startswith(code + ".") or canonical.startswith(code + "("):
            candidates.append(mixed + canonical[len(code) :])
    variants: list[str] = []
    for v in candidates:
        if v not in variants and canonical_ref(v) == canonical:
            variants.append(v)
    return variants


# --------------------------------------------------------------------------- withdrawal keys


def withdrawal_key(canonical: str | None, circular_date: date, title: str | None) -> str:
    """The identity of one withdrawn circular (decided 19 November 2026).

    A reference alone is not unique: RBI lists some notifications once per amendment under the
    original number and date ("Amended upto 01.10.2002 - ..."), and 154 rows carry no reference
    at all ("Mail Box Clarification"). Reference, circular date and title together are unique, and
    rows equal on all three are one circular listed twice. The key is the sha256 of the three,
    the reference canonical (empty when there is none) and the title as published.
    """
    if canonical is not None and canonical_ref(canonical) != canonical:
        raise InvalidReferenceError(f"not a canonical reference: {canonical!r}")
    material = "\x1f".join((canonical or "", circular_date.isoformat(), title or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
