"""Step 3, the paragraph parser: extracted HTML blocks to Phase 0 ``Paragraph`` records.

A state machine over a direction's blocks, built on the conventions observed across the corpus
(evals/recon-rbi-20261119.md) and the anchors decided on 19 November 2026 (``anchors.py``):

``front``    masthead (RBI number, date), "Previous Versions", the title, the table of contents
``preamble`` text before the first numbered paragraph: the enacting formula, an introduction
``body``     numbered paragraphs, under chapter and section headings
``closing``  the signature and the footnotes after it
``annex``    an annex printed in the page; its numbering starts again

A paragraph starts at a block whose text opens with a printed number that continues the
numbering (``7`` after ``6``, ``62A`` after ``62``, ``4.1`` under ``4``), and takes in every
following block until the next one: sub-paragraphs, provisos, lists (with the marker a browser
draws), tables and the numbered notes under them. A numbered-looking block that does not continue
the numbering, such as "1. For a bank, the Total under column 3 ..." under a table in paragraph 10,
stays inside the paragraph it sits in. Nothing is dropped: text the machine cannot place is
appended to the paragraph before it and reported as a warning.

Not paragraphs, and counted separately: headings (they make the ``heading_path``), the table of
contents, footnotes (``<p class="footnote">``, or any block a footnote marker links to), and the
closing. Footnote markers glued to the text ("1These Directions") are removed from paragraph
bodies; every other character is kept as rendered.

The parser reads only the page. It never infers a field the text does not state: applicability,
dates and versions that a page does not print are left empty and reported as missing.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

from extant.core.authority import AuthorityRank
from extant.core.hashing import sha256_hex
from extant.core.identity import make_block_anchor, make_id_anchor, make_para_id, parse_doc_id
from extant.core.models import Document, Paragraph
from extant.corpora.rbi.anchors import PREAMBLE, annex_anchor, paragraph_anchor, printed_number
from extant.corpora.rbi.entities import CATEGORIES, subtypes_in
from extant.corpora.rbi.extract_html import Block, HtmlDocument, extract_html

PARSER_VERSION = "2"  # 2: applicability subtracts the entities a direction excludes

# parse_status, a closed vocabulary
PARSED = "parsed"  # every block placed, numbering continuous
PARSED_WITH_WARNINGS = "parsed_with_warnings"  # parsed; numbering gaps or blocks absorbed
NO_BODY = "no_body"  # the page carries no direction text
NO_PARAGRAPHS = "no_paragraphs"  # the page has text but no numbered paragraph was found
FAILED = "failed"  # the parser raised; the record says where
STATUSES = (PARSED, PARSED_WITH_WARNINGS, NO_BODY, NO_PARAGRAPHS, FAILED)

# "62A.", "4.1", "38 A." (a spaced inserted letter takes a dot), but "12 ALM shall ..." is 12.
_NUMBER = re.compile(
    r"^(?P<num>\d+(?:\.\d+)*(?:[A-Z]{1,3}\b|\s[A-Z](?=\.))?)(?P<sep>\.\s*|\s+)(?=\S)"
)
_CHAPTER = re.compile(r"^(?:Chapter|CHAPTER|Part|PART)[\s\-\u2013\u2014]*(?P<n>[IVXLC]+|\d+)\b")
_SECTION = re.compile(
    r"^[A-Z]{1,2}\.(?:\d+(?:\.\d+)*)?\s+[A-Z(]"
)  # "A. Short Title"; the dot is required
_DATE_LINE = re.compile(r"^[A-Z][a-z]+\s+\d{1,2},\s+\d{4}$")
_SIGNATURE = re.compile(r"^\((?:Dr\.?|Shri|Smt\.?|Ms\.?|Mr\.?)?\s*[A-Z][A-Za-z.\s\-]{2,60}\)$")
_DESIGNATION = re.compile(
    r"(?i)\b(?:general manager|executive director|deputy governor|governor|director"
    r"|officer-in-charge)\b"
)
_TITLE = re.compile(
    r"^(?:Reserve Bank of India|Master Direction)\b.*\b(?:Directions?|Guidelines)\b", re.S
)
_UPDATED = re.compile(
    r"\s*[(\[]?\s*updated\s+as\s+on\s+[A-Z][a-z]+\s+\d{1,2},\s+\d{4}\s*[)\]]?", re.I
)
_TOC = re.compile(r"^\s*Table of Contents", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Number:
    """A printed paragraph number, as a sortable tuple: ``62A`` is (62, "A", ())."""

    base: int
    letter: str
    decimals: tuple[int, ...]

    @classmethod
    def parse(cls, printed: str) -> Number:
        s = printed_number(printed)
        m = re.fullmatch(r"(\d+)([A-Z]{0,3})((?:\.\d+[A-Z]{0,3})*)", s)
        if m is None:  # pragma: no cover - printed_number already validated the shape
            raise ValueError(printed)
        decimals = tuple(int(re.match(r"\d+", d).group()) for d in m.group(3).split(".")[1:])  # type: ignore[union-attr]
        return cls(int(m.group(1)), m.group(2), decimals)

    def continues(self, last: Number | None) -> bool:
        """Whether this number can follow ``last`` in one run of numbering: the next
        paragraph, an inserted letter, a decimal child, or a jump forward (a gap)."""
        if last is None:
            return True
        if self.decimals and not last.decimals:
            return self.base == last.base and self.letter == last.letter
        if self.decimals and last.decimals:
            if (self.base, self.letter) == (last.base, last.letter):
                return self.decimals > last.decimals
            return self.base > last.base
        if self.base == last.base and not last.decimals:
            return self.letter > last.letter
        # An inserted letter follows its own base (15A after 15). A lettered number that jumps
        # ahead, such as "15A." inside paragraph 6's definitions, is a sub-item, not a paragraph.
        return self.base > last.base and not self.letter


@dataclass(frozen=True, slots=True)
class Warning:
    code: str  # numbering_gap | absorbed_numbered_block | text_before_first_heading ...
    anchor: str
    detail: str


@dataclass
class _Open:
    anchor: str
    number: str | None
    chapter: str | None
    heading_path: str | None
    first_block: Block
    parts: list[str] = field(default_factory=list)


@dataclass
class ParsedDocument:
    doc_id: str
    status: str
    document: Document | None
    paragraphs: list[Paragraph]
    chapters: list[str]
    annexes: list[str]
    applicability: list[str]  # entity codes the page's applicability text names
    applicability_status: str  # extracted | partial | missing
    applicability_text: str | None
    issued_on: date | None
    effective_from: date | None
    effective_basis: str  # dated | issue date | not dated | not stated
    updated_as_on: date | None
    footnotes: int
    closing_blocks: int
    warnings: list[Warning]
    failure: str | None = None
    failure_anchor: str | None = None
    title: str | None = None
    title_source: str | None = None  # page | listing | None
    page_furniture: int = 0  # "Previous Versions" and one-character headings, skipped
    # how every block of the page was placed: front, toc, heading, body, footnote, closing,
    # furniture, empty. The values add up to the page's block count, or the parse is suspect.
    block_accounting: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers


def _anchor_of(block: Block) -> str:
    for element_id in block.ids:
        try:
            return make_id_anchor(element_id)
        except ValueError:
            continue
    return make_block_anchor(block.index)


def _body_text(block: Block) -> str:
    """A block's text as it belongs in a paragraph body: footnote markers removed, the list
    marker a browser draws put back, table rows set out one per line."""
    if block.kind == "table" and block.rows is not None:
        return "\n".join(" | ".join(cell for cell in row) for row in block.rows if any(row))
    text = block.text
    for marker in sorted(block.markers, key=lambda m: m.start, reverse=True):
        if marker.href or marker.title:
            text = text[: marker.start] + text[marker.end :]
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if block.kind == "list_item" and block.list_marker:
        text = f"{block.list_marker} {text}" if text else block.list_marker
    return text


def _probe(block: Block) -> str:
    """The text the machine reads structure from: footnote markers removed and an opening
    amendment bracket ignored, so "13[15A. In exercise ..." (marker 13, then RBI's bracket
    around substituted text) is read as paragraph 15A."""
    text = block.text
    for marker in sorted(block.markers, key=lambda m: m.start, reverse=True):
        if marker.href or marker.title:
            text = text[: marker.start] + text[marker.end :]
    return text.strip().lstrip("[").lstrip()


def _date(text: str) -> date | None:
    try:
        return datetime.strptime(re.sub(r"\s+", " ", text.strip()), "%B %d, %Y").date()
    except ValueError:
        return None


_CATEGORY_PATTERNS = {
    c.code: re.compile(
        {
            "cb": r"commercial banks?|corresponding new banks?",
            "sfb": r"small finance banks?|\bSFBs?\b",
            "pb": r"payments? banks?",
            "lab": r"local area banks?|\bLABs?\b",
            "rrb": r"regional rural banks?|\bRRBs?\b",
            "ucb": r"urban co-?operative banks?|\bUCBs?\b",
            "rcb": r"rural co-?operative banks?|state co-?operative banks?"
            r"|central co-?operative banks?",
            "aifi": r"all[ -]india financial institutions?|\bAIFIs?\b",
            "nbfc": r"non-banking financial compan(?:y|ies)|\bNBFCs?\b",
            "arc": r"asset reconstruction compan(?:y|ies)|\bARCs?\b",
            "cic": r"credit information compan(?:y|ies)",
        }[c.code],
        re.IGNORECASE,
    )
    for c in CATEGORIES
}
_APPLICABILITY = re.compile(
    r"(?i)\b(?:shall|will)\s+(?:be\s+)?(?:applicable|apply)\b|\bapplicable to\b"
)
_COMMENCEMENT = re.compile(
    r"(?i)\b(?:come|came)\s+into\s+(?:force|effect)\b|\bshall\s+(?:be|become)\s+effective\b|\bbecome\s+effective\b"
)
_ISSUE_DATE_FORMS = re.compile(
    r"(?i)immediate effect|immediately|date of (?:its |their )?issu(?:e|ance)"
    r"|from the date of issue"
)
_WEBSITE_FORM = re.compile(r"(?i)placed on the official website")
_DATE_IN_TEXT = re.compile(r"[A-Z][a-z]+\s+\d{1,2},\s+\d{4}")


# Exclusions inside an applicability paragraph. Three forms are subtracted, each measured on
# the corpus: a bracketed one, "banking companies (other than Small Finance Banks, ...)" and
# "[excluding Housing Finance Companies (HFCs)]"; a list after the word, "excluding Small Finance
# Banks (SFBs), ... and Regional Rural Banks (RRBs), hereinafter ...", up to "hereinafter", a
# semicolon or the end of the sentence, and only when the list starts with an entity; and the
# directions as a whole being "not applicable to the following: (i) ... (ii) ...", up to the
# next numbered sub-paragraph or "Note:", or "not applicable for X" up to the end of its clause.
# An exclusion of parts only ("paragraphs 29 to 37 ... shall not apply to Government owned
# NBFCs", "except directions contained in Chapter IX", "except 6(3) and 6(4)") leaves the
# entity in scope.
_EXCLUDED_BRACKET = re.compile(
    r"(?i)[(\[]\s*(?:other than|excluding|except|but not)\b"
    r"[^()\[\]]*(?:\([^()]*\)[^()\[\]]*)*[)\]]"
)
_EXCLUDED_LIST = re.compile(
    r"(?i)\b(?:excluding|other than|except(?:\s+for)?)\s+"
    r"(?P<list>.*?)(?=,?\s*\(?hereinafter|;|\.(?:\s|$)|$)",
    re.DOTALL,
)
_NOT_APPLICABLE = re.compile(
    # not "the regulations prescribed in these directions shall not be applicable", a part only
    r"(?i)(?<!in )(?<!of )(?<!under )\b(?:these|this)\s+directions?\s+"
    r"(?:are|is|shall|will)\s+not\s+(?:be\s+)?"
    r"(?:applicable|apply)\s+(?:to|for)\b"
    r"(?:\s+the\s+following\b.*?(?=\(\d+\)\s|\bNote\s*:|$)|.*?(?=;|\.(?:\s|$)|$))",
    re.DOTALL,
)


def _names_entity(text: str) -> bool:
    return any(p.search(text) for p in _CATEGORY_PATTERNS.values()) or bool(subtypes_in(text))


def in_scope_text(text: str) -> str:
    """An applicability paragraph less the entities it excludes (the three forms above)."""
    text = _EXCLUDED_BRACKET.sub(" ", text)
    text = _EXCLUDED_LIST.sub(
        lambda m: " " if _names_entity(m.group("list")[:40]) else m.group(0), text
    )
    return _NOT_APPLICABLE.sub(" ", text)


def applicability_codes(text: str) -> list[str]:
    """Entity codes an applicability paragraph brings into scope: those it names, less those it
    excludes; the 11 categories first, then NBFC subtypes."""
    scope = in_scope_text(text)
    codes = [code for code, pattern in _CATEGORY_PATTERNS.items() if pattern.search(scope)]
    return codes + sorted(subtypes_in(scope))


def commencement(text: str, issued_on: date | None) -> tuple[date | None, str]:
    """``effective_from`` and the basis it rests on, from a commencement sentence."""
    if _WEBSITE_FORM.search(text):
        return None, "not dated"
    after = text[_COMMENCEMENT.search(text).end() :] if _COMMENCEMENT.search(text) else text  # type: ignore[union-attr]
    m = _DATE_IN_TEXT.search(after)
    if m is not None and re.search(
        r"(?i)\b(?:from|on|w\.e\.f\.?|with effect from)\s*$", after[: m.start()]
    ):
        return _date(m.group(0)), "dated"
    if _ISSUE_DATE_FORMS.search(after):
        return issued_on, "issue date"
    return None, "not stated"


# --------------------------------------------------------------------------- the machine


def _is_footnote(block: Block, footnote_ids: set[str]) -> bool:
    return block.kind == "footnote" or bool(set(block.ids) & footnote_ids)


def _is_toc(block: Block) -> bool:
    if block.kind == "table" and block.rows:
        return bool(block.rows[0]) and _TOC.match(block.rows[0][0] or "") is not None
    return _TOC.match(block.text) is not None and len(block.text) < 40


def _is_chapter(block: Block) -> bool:
    return (
        block.kind in ("heading", "paragraph")
        and len(block.text) < 200
        and "\n" not in block.text.strip()
        and _CHAPTER.match(_probe(block)) is not None
    )


def _is_annex(block: Block) -> bool:
    first = block.text.split("\n", 1)[0].strip()
    if block.kind not in ("heading", "paragraph") or len(first) >= 40:
        return False
    try:
        annex_anchor(first)  # the one rule for annex headings; "Annexures" is not one
    except ValueError:
        return False
    return True


def _is_signature(blocks: list[Block], i: int) -> bool:
    """A name in brackets with a designation under it, in the same block or the next."""
    text = blocks[i].text.strip()
    if _SIGNATURE.match(text.split("\n", 1)[0].strip()) is None:
        return False
    below = text.split("\n", 1)[1] if "\n" in text else ""
    if not below and i + 1 < len(blocks):
        below = blocks[i + 1].text
    return _DESIGNATION.search(below[:120]) is not None


def _is_section(block: Block) -> bool:
    text = _probe(block)
    if _SECTION.match(text) is None or len(text) > 160:
        return False
    if block.kind == "heading":
        return True
    # a plain paragraph that reads like a heading: short and not a sentence
    return block.kind == "paragraph" and not text.endswith((".", ":", ";")) and len(text) < 120


def parse_blocks(
    doc: HtmlDocument,
    *,
    doc_id: str,
    fallback_title: str | None = None,
    source_url: str | None = None,
    supersedes: str | None = None,
) -> ParsedDocument:
    """Parse one extracted page. Never raises: a failure is a FAILED record with the anchor of
    the block it happened at."""
    parse_doc_id(doc_id)
    empty = ParsedDocument(
        doc_id, NO_BODY, None, [], [], [], [], "missing", None, None, None, "not stated", None,
        0, 0, [],
    )  # fmt: skip
    if doc.status != "extracted" or not doc.blocks:
        return empty
    try:
        return _parse(
            doc,
            doc_id,
            fallback_title,
            _source_url(source_url, doc.url),
            supersedes,
        )
    except Exception as exc:  # any failure becomes a record, never a crash
        empty.status = FAILED
        empty.failure = f"{type(exc).__name__}: {exc}"
        anchor = getattr(exc, "anchor", None)
        empty.failure_anchor = anchor
        return empty


def _source_url(given: str | None, page_url: str) -> str:
    """The URL a Document cites: the caller's, else the page's when it is a real address, else
    RBI's site. Never a reason to fail a parse whose paragraphs are sound."""
    for candidate in (given, page_url):
        if candidate and re.match(r"^https?://\S+$", candidate):
            return candidate
    return "https://www.rbi.org.in/"


class _ParseError(Exception):
    def __init__(self, message: str, anchor: str) -> None:
        super().__init__(f"{message} (at {anchor})")
        self.anchor = anchor


def _parse(
    doc: HtmlDocument,
    doc_id: str,
    fallback_title: str | None,
    source_url: str,
    supersedes: str | None,
) -> ParsedDocument:
    blocks = doc.blocks
    footnote_ids = {
        m.href.lstrip("#") for b in blocks for m in b.markers if m.href and m.href.startswith("#")
    }
    warnings: list[Warning] = []

    # --- front matter: masthead, date line, the title, the table of contents
    issued_on: date | None = None
    title: str | None = None
    start = 0
    for i, b in enumerate(blocks[:8]):
        if issued_on is None and _DATE_LINE.match(b.text.strip()):
            issued_on = _date(b.text)
        if title is None and _TITLE.match(b.text.strip()) and not _is_chapter(b):
            title = b.text
            start = i + 1
            break
    title_source: str | None = "page" if title is not None else None
    if title is None:
        # A broken page (13610's title heading is the single letter "c"). Parse it anyway, from
        # after the date line, and take the title from the listing if the caller has it.
        warnings.append(Warning("no_title", _anchor_of(blocks[0]), "no title in the first blocks"))
        start = next(
            (i + 1 for i, b in enumerate(blocks[:6]) if _DATE_LINE.match(b.text.strip())), 0
        )
        if fallback_title:
            title, title_source = fallback_title, "listing"
    updated_as_on = None
    m = re.search(r"(?i)updated\s+as\s+on\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", title or "")
    if m:
        updated_as_on = _date(m.group(1))

    paragraphs: list[_Open] = []
    chapters: list[str] = []
    annexes: list[str] = []
    preamble_parts: list[str] = []
    preamble_first: Block | None = None
    open_: _Open | None = None
    last: Number | None = None
    chapter_numeral: str | None = None
    chapter_text: str | None = None
    section_text: str | None = None
    annex_base: str | None = None
    annex_text: str | None = None
    region = "preamble"
    footnotes = 0
    closing_blocks = 0

    def heading_path() -> str | None:
        parts = [p for p in (annex_text, chapter_text, section_text) if p]
        return " > ".join(parts) if parts else None

    def close() -> None:
        nonlocal open_
        if open_ is not None:
            paragraphs.append(open_)
            open_ = None

    def peek_next_number(i: int) -> Number | None:
        for b in blocks[i + 1 : i + 4]:
            if _is_footnote(b, footnote_ids) or b.kind == "table":
                continue
            m = _NUMBER.match(_probe(b))
            if m:
                try:
                    return Number.parse(m.group("num"))
                except ValueError:
                    return None
            return None
        return None

    def introduces_structure(i: int) -> bool:
        """Whether the heading at ``i`` introduces the next numbered paragraph, a chapter or an
        annex. A heading that is followed by ordinary text, such as "(1) Where promoters are
        individuals" or "Explanation:", belongs to the paragraph it sits in."""
        for nb in blocks[i + 1 : i + 7]:
            t = _probe(nb)
            if not t or _is_toc(nb) or _is_footnote(nb, footnote_ids):
                continue
            if _is_chapter(nb) or _is_annex(nb):
                return True
            nm = _NUMBER.match(t)
            if nm is not None and nb.kind in ("paragraph", "heading"):
                try:
                    return Number.parse(nm.group("num")).continues(last)
                except ValueError:
                    return False
            if nb.kind == "heading" or _is_section(nb):
                continue  # a stack of headings: look past it
            return False
        return False

    def _jump_is_real(number: Number, i: int) -> bool:
        """A forward jump (64 then 100) is a gap only if the numbering does not carry on from
        before it: when the next number seen is 65, the "100" was text, not a paragraph."""
        if last is None or number.decimals or number.letter or number.base <= last.base + 1:
            return True
        for nb in blocks[i + 1 : i + 25]:
            nm = _NUMBER.match(_probe(nb))
            if (
                nm is None
                or nb.kind not in ("paragraph", "heading", "text")
                or _is_footnote(nb, footnote_ids)
            ):
                continue
            try:
                nxt = Number.parse(nm.group("num"))
            except ValueError:
                continue
            if nxt.base == last.base + 1 and not nxt.letter and not nxt.decimals:
                return False
            if nxt.base > last.base + 1:
                return True
        return True

    junk = 0
    # every block is placed in exactly one bucket; the buckets must add up to the page
    placed: Counter[str] = Counter(front=start)
    pending: list[Block] = []  # chapter introduction text waiting for the chapter's first paragraph
    pending_chapter: str | None = None

    def flush_pending() -> None:
        """A chapter ended before any paragraph: its introduction goes to the paragraph before."""
        nonlocal pending
        for pb in pending:
            if paragraphs:
                warnings.append(
                    Warning(
                        "text_between_paragraphs",
                        _anchor_of(pb),
                        f"kept in {paragraphs[-1].anchor}",
                    )
                )
                paragraphs[-1].parts.append(_body_text(pb))
            else:
                preamble_parts.append(_body_text(pb))
        pending = []

    for i in range(start, len(blocks)):
        b = blocks[i]
        text = _probe(b)
        if not text:
            placed["empty"] += 1
            continue
        if _is_toc(b):
            placed["toc"] += 1
            continue
        if text == "Previous Versions" or (b.kind == "heading" and len(text) <= 2):
            junk += 1  # page furniture, not text of the direction
            placed["furniture"] += 1
            continue
        if _is_footnote(b, footnote_ids):
            footnotes += 1
            placed["footnote"] += 1
            continue
        if _is_annex(b):
            close()
            flush_pending()
            region = "annex"
            annex_base = annex_anchor(text.split("\n", 1)[0])
            annex_text = text.replace("\n", " ")
            annexes.append(annex_text)
            placed["heading"] += 1
            chapter_text = section_text = None
            last = None
            rest = text.split("\n", 1)[1].strip() if "\n" in text else ""
            open_ = _Open(annex_base, None, None, heading_path(), b, [rest] if rest else [])
            continue
        if region == "closing":
            closing_blocks += 1
            placed["closing"] += 1
            continue
        if (paragraphs or open_) and region != "annex" and _is_signature(blocks, i):
            close()
            flush_pending()
            region = "closing"
            closing_blocks += 1
            placed["closing"] += 1
            continue
        if _is_chapter(b):
            close()
            flush_pending()
            pending_chapter = text
            chapter_numeral = _CHAPTER.match(text).group("n")  # type: ignore[union-attr]
            chapter_text = text
            section_text = None
            chapters.append(text)
            placed["heading"] += 1
            if region == "preamble":
                region = "body"
            continue
        number_match = _NUMBER.match(text)
        number: Number | None = None
        if number_match is not None and b.kind in ("paragraph", "heading", "text"):
            try:
                number = Number.parse(number_match.group("num"))
            except ValueError:
                number = None
        if number is not None and number.continues(last) and _jump_is_real(number, i):
            following = peek_next_number(i)
            if (
                b.kind == "heading"
                and following is not None
                and following.base == number.base
                and following.letter == number.letter
                and following.decimals
            ):
                # "4. Definitions" introducing 4.1, 4.2: a heading, not a paragraph
                close()
                section_text = text
                placed["heading"] += 1
                last = number
                if region == "preamble":
                    region = "body"
                continue
            close()
            if (
                last is not None
                and not number.decimals
                and not number.letter
                and number.base > last.base + 1
            ):
                warnings.append(
                    Warning("numbering_gap", _anchor_of(b), f"{_fmt(last)} then {_fmt(number)}")
                )
            printed = number_match.group("num")  # type: ignore[union-attr]
            anchor = (
                f"{annex_base}.{paragraph_anchor(printed)}" if region == "annex" and annex_base
                else paragraph_anchor(printed)
            )  # fmt: skip
            if (
                region == "annex"
                and paragraphs
                and paragraphs[-1].anchor == annex_base
                and not paragraphs[-1].parts
            ):
                paragraphs.pop()  # the bare annex heading gives way to its numbered paragraphs
            placed["body"] += 1  # the block that opens the paragraph
            intro = list(pending)
            if intro:
                warnings.append(
                    Warning(
                        "chapter_introduction",
                        _anchor_of(intro[0]),
                        f"{len(intro)} block(s) before the chapter's first paragraph,"
                        f" kept in {anchor}",
                    )
                )
            open_ = _Open(
                anchor,
                printed_number(printed),
                chapter_numeral,
                heading_path(),
                intro[0] if intro else b,
                [_body_text(pb) for pb in intro] + [_body_text(b)],
            )
            pending.clear()
            pending_chapter = None
            last = number
            if region == "preamble":
                region = "body"
            continue
        if number is not None and open_ is not None:
            warnings.append(
                Warning(
                    "absorbed_numbered_block",
                    _anchor_of(b),
                    f"{_fmt(number)} does not continue {_fmt(last)}; kept in {open_.anchor}",
                )
            )
        heading_like = b.kind == "heading" or (b.kind == "paragraph" and _is_section(b))
        if (
            heading_like
            and (region in ("body", "annex") or chapters)
            and (open_ is None or introduces_structure(i))
        ):
            close()
            section_text = text
            placed["heading"] += 1
            if region == "preamble":
                region = "body"
            continue
        if open_ is not None:
            open_.parts.append(_body_text(b))
            placed["body"] += 1
            continue
        if region == "preamble":
            if preamble_first is None:
                preamble_first = b
            preamble_parts.append(_body_text(b))
            placed["body"] += 1
            continue
        # text in the body with no open paragraph, such as a chapter's "Background: ..." before its
        # first numbered paragraph: held, and given to that paragraph
        if region == "body" and chapters and (pending_chapter == chapter_text):
            pending.append(b)
            placed["body"] += 1
            continue
        if paragraphs:
            warnings.append(
                Warning(
                    "text_between_paragraphs", _anchor_of(b), f"kept in {paragraphs[-1].anchor}"
                )
            )
            paragraphs[-1].parts.append(_body_text(b))
            placed["body"] += 1
        else:
            if preamble_first is None:
                preamble_first = b
            preamble_parts.append(_body_text(b))
            placed["body"] += 1
    close()
    flush_pending()

    built: list[_Open] = []
    if preamble_parts and preamble_first is not None:
        built.append(_Open(PREAMBLE, None, None, None, preamble_first, preamble_parts))
    built.extend(paragraphs)

    records: list[Paragraph] = []
    seen: set[str] = set()
    for ordinal, p in enumerate(built):
        body = "\n".join(part for part in p.parts if part).strip()
        if not body:
            warnings.append(Warning("empty_paragraph", p.anchor, "no text; kept as its heading"))
            body = p.heading_path or p.anchor
        if p.anchor in seen:
            raise _ParseError(f"anchor {p.anchor} would be used twice", _anchor_of(p.first_block))
        seen.add(p.anchor)
        records.append(
            Paragraph(
                para_id=make_para_id(doc_id, p.anchor),
                doc_id=doc_id,
                ordinal=ordinal,
                chapter=p.chapter,
                para_number=p.number,
                heading_path=p.heading_path,
                body=body,
                content_hash=sha256_hex(body),
                source_page=None,
                source_anchor=_anchor_of(p.first_block),
            )
        )

    # --- metadata the page states
    app_text = next((r.body for r in records[:20] if _APPLICABILITY.search(r.body)), None)
    codes = applicability_codes(app_text) if app_text else []
    app_status = "extracted" if codes else ("partial" if app_text else "missing")
    com_text = next((r.body for r in records[:20] if _COMMENCEMENT.search(r.body)), None)
    effective_from, basis = commencement(com_text, issued_on) if com_text else (None, "not stated")

    numbered = [r for r in records if r.para_number is not None]
    if not numbered:
        status = NO_PARAGRAPHS
    elif any(
        w.code in ("numbering_gap", "absorbed_numbered_block", "text_between_paragraphs")
        for w in warnings
    ):
        status = PARSED_WITH_WARNINGS
    else:
        status = PARSED

    parts = parse_doc_id(doc_id)
    clean_title = _UPDATED.sub("", title).strip() if title else None
    document: Document | None = None
    # A Document needs a title, and after v1 the version it supersedes; without them the
    # paragraphs still stand and the coverage report counts the missing record.
    if clean_title and (parts.version == 1 or supersedes is not None):
        document = Document(
            doc_id=doc_id,
            corpus="rbi",
            title=clean_title,
            instrument_type=_instrument(clean_title),
            authority_rank=AuthorityRank.MASTER_DIRECTION,
            issued_on=issued_on,
            effective_from=effective_from,
            supersedes_doc_id=supersedes,
            version_seq=parts.version,
            source_url=source_url,
            source_sha256=doc.source_sha256,
            parse_status=status,
        )
    return ParsedDocument(
        doc_id, status, document, records, chapters, annexes, codes, app_status, app_text,
        issued_on, effective_from, basis, updated_as_on, footnotes, closing_blocks, warnings,
        title=clean_title, title_source=title_source, page_furniture=junk,
        block_accounting=dict(placed),
    )  # fmt: skip


def _instrument(title: str) -> str:
    for word in ("Supervisory Directions", "Directions", "Direction", "Guidelines", "Scheme"):
        if re.search(rf"\b{word}\b", title):
            return word.lower().replace(" ", "_")
    return "direction"


def _fmt(n: Number | None) -> str:
    if n is None:
        return "nothing"
    return f"{n.base}{n.letter}" + "".join(f".{d}" for d in n.decimals)


def parse_document(source_html: bytes, *, doc_id: str) -> ParsedDocument:
    """Extract and parse one page: the entry point the parser gate uses."""
    from extant.core.hashing import sha256_hex_bytes

    doc = extract_html(source_html, source_sha256=sha256_hex_bytes(source_html), url="")
    return parse_blocks(doc, doc_id=doc_id)
