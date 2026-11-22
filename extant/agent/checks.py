"""Deterministic verification of composed claims. No model is called here.

Four checks, each a hard failure:

1. Citation existence: every cited para_id is a candidate the retrieval layer returned
   from the index, with its body in hand.
2. Quote match: every quote is a literal substring of the body of a paragraph the claim
   cites. The model supplies the substring; this code supplies the offsets.
3. Supersession: nothing cited is out of force on the date it was asked for. The gate
   already removed such paragraphs; this check stays so that the two fail independently.
4. Numerics: every number in the claim appears in one of its quotes (``numerics``).

Then the first groundedness pass: the share of the claim's content words that also appear
in its quotes. A claim below the threshold has passed the substring check but says more than
its quotes plainly show, so it goes to the judge for a second opinion.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from extant.agent import numerics
from extant.agent.models import Candidate, CheckResult, Citation, Claim, DraftClaim
from extant.core.identity import DOC_ID_RE

# Labels the asker must never read in a claim: the composer's bracketed candidate numbers
# ("paragraph [3]"), and identifiers, which belong in the citation and not in prose.
_INTERNAL_LABEL = re.compile(r"\[\s*\d+\s*\]|" + DOC_ID_RE.pattern)

log = logging.getLogger("extant.agent.checks")

OVERLAP_THRESHOLD = 0.5

_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "which",
        "who",
        "whom",
        "whose",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "to",
        "into",
        "onto",
        "upon",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "shall",
        "will",
        "would",
        "should",
        "may",
        "might",
        "must",
        "can",
        "could",
        "not",
        "no",
        "nor",
        "any",
        "all",
        "each",
        "every",
        "its",
        "it",
        "their",
        "them",
        "they",
        "there",
        "here",
        "such",
        "per",
        "under",
        "over",
        "between",
        "within",
        "without",
        "about",
        "also",
        "only",
        "other",
        "than",
        "so",
        "very",
        "more",
        "most",
        "less",
        "least",
        "same",
        "any",
        "some",
        "what",
        "when",
        "where",
        "how",
        "why",
        "whether",
        "while",
    ]
)
_WORD = re.compile(r"[a-z][a-z0-9]+")


class QuoteNotFoundError(ValueError):
    def __init__(self, claim: DraftClaim, quote: str, para_ids: Sequence[str]) -> None:
        super().__init__(f"quote {quote!r} is in none of {list(para_ids)} (claim {claim.text!r})")


def _stem(word: str) -> str:
    for suffix in ("ies", "es", "s", "ed", "ing"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def content_words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP}


def overlap(claim: str, quotes: Sequence[str]) -> float:
    """Share of the claim's content words found in its quotes; 1.0 for a claim with none."""
    words = content_words(claim)
    if not words:
        return 1.0
    quoted = set().union(*(content_words(q) for q in quotes)) if quotes else set()
    return len(words & quoted) / len(words)


@dataclass(frozen=True)
class Located:
    candidate: Candidate
    quote: str
    start: int
    end: int


def locate(claim: DraftClaim, by_id: dict[str, Candidate]) -> list[Located]:
    """Every quote found in one of the claim's cited paragraphs, or QuoteNotFoundError."""
    out = []
    for quote in claim.quotes:
        for pid in claim.citation_ids:
            start = by_id[pid].body.find(quote)
            if start != -1:
                out.append(Located(by_id[pid], quote, start, start + len(quote)))
                break
        else:
            raise QuoteNotFoundError(claim, quote, claim.citation_ids)
    return out


def citation(found: Located) -> Citation:
    c = found.candidate
    return Citation(
        para_id=c.para_id,
        doc_id=c.doc_id,
        doc_title=c.doc_title,
        paragraph_number=c.paragraph_number or c.para_id.split("#", 1)[1],
        effective_from=c.valid_from,
        effective_to=c.valid_to,
        entity_scope=list(c.entity_scope),
        authority_rank=c.authority_rank,
        binding=c.binding,
        source_url=c.source_url,  # type: ignore[arg-type]
        source_page=c.source_page,
        quote_start=found.start,
        quote_end=found.end,
    )


@dataclass(frozen=True)
class ClaimVerdict:
    index: int
    checks: dict[str, CheckResult]
    claim: Claim | None  # the verified claim, when every hard check passed
    overlap: float

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks.values())


def verify_claim(i: int, claim: DraftClaim, candidates: Sequence[Candidate]) -> ClaimVerdict:
    by_id = {c.para_id: c for c in candidates}
    checks: dict[str, CheckResult] = {}
    labels = [m.group(0) for m in _INTERNAL_LABEL.finditer(claim.text)]
    if labels:
        log.warning("plain text check failed: %s | claim=%r", labels, claim.text)
    checks["plain_text"] = CheckResult(
        name="plain_text",
        passed=not labels,
        detail=f"internal labels in the claim text: {labels} | claim {claim.text!r}"
        if labels
        else "",
    )
    missing = [p for p in claim.citation_ids if p not in by_id or not by_id[p].body]
    checks["citation_exists"] = CheckResult(
        name="citation_exists",
        passed=not missing,
        detail=f"not candidates: {missing}" if missing else "",
    )
    if missing:
        return ClaimVerdict(i, checks, None, 0.0)
    try:
        located = locate(claim, by_id)
        checks["quote_match"] = CheckResult(name="quote_match", passed=True)
    except QuoteNotFoundError as exc:
        checks["quote_match"] = CheckResult(name="quote_match", passed=False, detail=str(exc))
        return ClaimVerdict(i, checks, None, 0.0)
    cited = [by_id[p] for p in claim.citation_ids]
    stale = [c.para_id for c in cited if not c.in_force()]
    checks["supersession"] = CheckResult(
        name="supersession",
        passed=not stale,
        detail=f"not in force on the date asked: {stale}" if stale else "",
    )
    # Document titles name instruments ("... Directions, 2025"); paragraph numbers are
    # excluded by the naming rule wherever the claim says "paragraph 13".
    names = [c.doc_title for c in cited]
    numeric = numerics.check(claim.text, [f.quote for f in located], ignore=names)
    checks["numerics"] = CheckResult(
        name="numerics",
        passed=numeric.passed,
        detail=""
        if numeric.passed
        else (
            f"unmatched {[str(q) for q in numeric.unmatched]} | claim {claim.text!r} |"
            f" quotes {[f.quote for f in located]!r}"
        ),
    )
    score = overlap(claim.text, [f.quote for f in located])
    ok = all(c.passed for c in checks.values())
    verified = None
    if ok:
        by_para: dict[str, Located] = {}
        for f in located:
            by_para.setdefault(f.candidate.para_id, f)
        verified = Claim(text=claim.text, citations=[citation(f) for f in by_para.values()])
    return ClaimVerdict(i, checks, verified, score)
