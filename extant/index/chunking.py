"""Chunk rows and the splitter for oversized paragraphs. Corpus-agnostic.

A corpus adapter turns its parsed paragraphs into ``ChunkDraft`` rows: one per paragraph,
or several for a paragraph over ``MAX_CHUNK_TOKENS``. A draft is a ``chunks`` row without its
vector; step 7 joins each draft to its vector in ``public.vec_cache`` by ``content_hash``.

Two text columns, one rule. ``body`` is the regulator's text: an unsplit paragraph's whole
body, or for a piece of a split paragraph an exact substring of it. ``embed_text`` is the
composed header plus that body, and is only ever embedded. Quotes, groundedness checks and the
generated ``fts`` column use ``body``, never ``embed_text``, so a composed header can never be
quoted back as though the regulator wrote it.

The splitter is part of the chunker's output, so any change to it must bump the corpus
adapter's ``CHUNKER_VERSION`` (``extant.corpora.rbi.chunk``); a golden test over the parser
gate fixtures fails when the output changes and the version does not.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from extant.core.authority import AuthorityRank
from extant.core.hashing import sha256_hex
from extant.core.models import Chunk

MAX_CHUNK_TOKENS = 900
# A stated approximation, not a tokenizer: deterministic, offline, and for English legal text
# close to the tokenizers of the hosted models. The size limit is "roughly 900 tokens".
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A ``chunks`` row before its vector is attached."""

    para_id: str
    sub_index: int
    doc_id: str
    ordinal: int
    body: str
    embed_text: str
    content_hash: str
    entity_codes: tuple[str, ...]
    valid_from: date
    valid_to: date | None
    authority_rank: int

    def to_json(self) -> dict[str, Any]:
        return {
            "authority_rank": self.authority_rank,
            "body": self.body,
            "content_hash": self.content_hash,
            "doc_id": self.doc_id,
            "embed_text": self.embed_text,
            "entity_codes": list(self.entity_codes),
            "ordinal": self.ordinal,
            "para_id": self.para_id,
            "sub_index": self.sub_index,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ChunkDraft:
        return cls(
            para_id=raw["para_id"],
            sub_index=raw["sub_index"],
            doc_id=raw["doc_id"],
            ordinal=raw["ordinal"],
            body=raw["body"],
            embed_text=raw["embed_text"],
            content_hash=raw["content_hash"],
            entity_codes=tuple(raw["entity_codes"]),
            valid_from=date.fromisoformat(raw["valid_from"]),
            valid_to=date.fromisoformat(raw["valid_to"]) if raw["valid_to"] else None,
            authority_rank=raw["authority_rank"],
        )

    def to_chunk(self, embedding: Sequence[float]) -> Chunk:
        """The validated Phase 0 record, with its vector attached."""
        return Chunk(
            para_id=self.para_id,
            sub_index=self.sub_index,
            doc_id=self.doc_id,
            ordinal=self.ordinal,
            body=self.body,
            embed_text=self.embed_text,
            content_hash=self.content_hash,
            entity_codes=self.entity_codes,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            authority_rank=AuthorityRank(self.authority_rank),
            embedding=tuple(embedding),
        )


def make_draft(
    *,
    para_id: str,
    sub_index: int,
    doc_id: str,
    ordinal: int,
    body: str,
    embed_text: str,
    entity_codes: Sequence[str],
    valid_from: date,
    valid_to: date | None,
    authority_rank: int,
) -> ChunkDraft:
    if not body.strip():
        raise ValueError(f"{para_id} piece {sub_index} has no text")
    if not embed_text.endswith(body):
        raise ValueError(f"{para_id} piece {sub_index}: embed_text must end with the body")
    if valid_to is not None and valid_to <= valid_from:
        raise ValueError(f"{para_id}: valid_to must be after valid_from")
    if not entity_codes:
        raise ValueError(f"{para_id}: a chunk applies to at least one entity type")
    return ChunkDraft(
        para_id=para_id,
        sub_index=sub_index,
        doc_id=doc_id,
        ordinal=ordinal,
        body=body,
        embed_text=embed_text,
        content_hash=sha256_hex(embed_text),
        entity_codes=tuple(entity_codes),
        valid_from=valid_from,
        valid_to=valid_to,
        authority_rank=authority_rank,
    )


# --------------------------------------------------------------------------- the splitter

# Words that end in a full stop without ending a sentence, as they appear in RBI text.
_ABBREVIATIONS = frozenset(
    {
        "no", "nos", "rs", "i.e", "e.g", "viz", "etc", "ltd", "pvt", "co", "corp", "inc",
        "dr", "mr", "mrs", "ms", "smt", "shri", "sr", "jr", "st", "cf", "vs", "sec", "cl",
        "para", "paras", "art", "govt", "dept", "annex", "ch", "w.e.f", "p.a", "fig", "vol",
        "approx", "max", "min", "u/s", "hon",
    }
)  # fmt: skip
# A sentence ends at . ? ! or ; followed by whitespace and a capital, a digit, an opening
# bracket or quote. Lines are boundaries too: list items and table rows are one per line.
_SENTENCE_END = re.compile(r"(?<=[.?!;])\s+(?=[A-Z0-9(\[\"'\u2018\u201c])")
_LAST_WORD = re.compile(r"([A-Za-z][A-Za-z./]*)\.$")
_ENUMERATOR = re.compile(r"(?:^|\s)\(?(?:[a-z]|[ivxlc]+|\d+)\)?[.)]$", re.IGNORECASE)


def _is_boundary(text: str, end: int) -> bool:
    """Whether the terminator just before ``end`` really ends a sentence."""
    head = text[max(0, end - 24) : end]
    if head.endswith(";") or head.endswith("?") or head.endswith("!"):
        return True
    m = _LAST_WORD.search(head)
    if m and m.group(1).lower() in _ABBREVIATIONS:
        return False
    # "(a)." "ii." "3." are enumerators, not the end of a sentence.
    return _ENUMERATOR.search(head) is None


def _units(text: str) -> list[tuple[int, int]]:
    """Sentence and line spans that tile ``text`` exactly, separators included."""
    cuts = {0, len(text)}
    for i, ch in enumerate(text):
        if ch == "\n":
            cuts.add(i + 1)
    for m in _SENTENCE_END.finditer(text):
        if _is_boundary(text, m.start()):
            cuts.add(m.end())
    points = sorted(cuts)
    return [(a, b) for a, b in itertools.pairwise(points) if b > a]


def _cap(text: str, units: list[tuple[int, int]], limit: int) -> list[tuple[int, int]]:
    """Split any single unit over the limit at whitespace, so every unit fits a piece."""
    max_chars = limit * CHARS_PER_TOKEN
    out: list[tuple[int, int]] = []
    for a, b in units:
        while estimate_tokens(text[a:b]) > limit:
            cut = text.rfind(" ", a + 1, a + max_chars)
            if cut <= a:
                cut = text.rfind("\n", a + 1, a + max_chars)
            if cut <= a:
                cut = a + max_chars
            out.append((a, cut))
            a = cut
        out.append((a, b))
    return out


def split_body(text: str, limit: int = MAX_CHUNK_TOKENS) -> list[str]:
    """``text`` whole when it fits, else pieces of at most ``limit`` tokens cut at sentence
    boundaries, each next piece starting with the previous piece's last sentence. Every piece
    is an exact substring of ``text`` with its outer whitespace trimmed."""
    if estimate_tokens(text) <= limit:
        return [text]
    units = _cap(text, _units(text), limit)
    spans: list[tuple[int, int]] = []
    i, n = 0, len(units)
    while True:
        j = i + 1
        while j < n and estimate_tokens(text[units[i][0] : units[j][1]]) <= limit:
            j += 1
        spans.append((units[i][0], units[j - 1][1]))
        if j >= n:
            break
        # One sentence of overlap, when it leaves room for at least one new sentence.
        overlap = j - 1 > i and estimate_tokens(text[units[j - 1][0] : units[j][1]]) <= limit
        i = j - 1 if overlap else j
    pieces = [text[a:b].strip() for a, b in spans]
    return [p for p in pieces if p]
