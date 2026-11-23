"""Sensitive data at admission: Indian identifiers found and replaced with a type token.

Users paste real identifiers into compliance questions without thinking about it. Every
question is redacted at the API boundary, before the agent state exists, before any
embedding, model call or log line; the middleware runs the same redactor over every model
prompt as a backstop for a call added later by someone who did not know this rule existed.

Each match becomes a type token (``<PAN>``), so the redacted question still reads sensibly
and a log line shows what kind of thing was removed without showing it. A false positive
here is cheap and a false negative is not, so where a pattern is ambiguous it redacts.

The patterns run in a fixed order, longest structure first: a GSTIN contains a PAN, and a
CIN contains digit runs, so the outer identifier must be replaced before its parts are seen.
Tokens contain no digits, so a later pattern never matches an earlier token.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# Context that makes a digit run an account number. Account numbers have no single format
# (9 to 18 digits, bank by bank), so the number is found by what precedes it.
_ACCOUNT_CONTEXT = (
    r"(?:bank\s+)?(?:account|a/c|acct|ac)(?:\s*(?:no|number|num|#))?\.?"
    r"|savings|beneficiary"
)


@dataclass(frozen=True)
class Pattern:
    kind: str
    regex: re.Pattern[str]
    group: str | int = 0  # the part replaced by the token; the rest of the match stays


# Digit patterns never start or end inside a word, a decimal or a reference ("21.04.048",
# "2025-26"), and never inside a run glued to letters, as in a hex digest or an identifier
# ("rbi:md:2025:0387:v1"). Long runs are taken before short ones (the order below), so a 16
# digit card number is one <ID_NUMBER>, never an Aadhaar number with four digits left over.
_NB = r"(?<![\w.,/])"
_NA = r"(?!\d)(?![.,/]\d)(?![A-Za-z_])"

# Case-insensitive throughout: a pasted PAN is as often lower case.
PATTERNS: tuple[Pattern, ...] = (
    # An email address, except the regulator's own public mailboxes (cisbi@rbi.org.in): 91
    # corpus paragraphs print one, and redacting them in a model prompt would break the
    # composer's verbatim quotes of that paragraph for nothing.
    Pattern(
        "EMAIL", re.compile(r"\b[\w.+-]+@(?!(?:[\w-]+\.)*rbi\.org\.in\b)[\w-]+(?:\.[\w-]+)+\b")
    ),
    # Corporate identity number: L or U, 5 digits (industry), 2 letters (state), 4 digits
    # (year), 3 letters (company type), 6 digits (registration).
    Pattern("CIN", re.compile(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b", re.IGNORECASE)),
    # GSTIN: 2 state digits, a PAN, an entity number, a default letter, a check character.
    Pattern("GSTIN", re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]{3}\b", re.IGNORECASE)),
    Pattern("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)),
    # IFSC: 4 letters (bank), a zero, 6 alphanumerics (branch).
    Pattern("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", re.IGNORECASE)),
    # A bank account number: 9 or more digits (separators allowed) after account words. No
    # upper bound: account numbers run to 18 digits, and a longer run after "a/c" is redacted
    # whole rather than cut at 18 with the rest left readable.
    Pattern(
        "ACCOUNT",
        re.compile(
            rf"\b(?:{_ACCOUNT_CONTEXT})[^\d\n]{{0,24}}?(?P<num>\d(?:[ -]?\d){{8,}})" + _NA,
            re.IGNORECASE,
        ),
        group="num",
    ),
    # Indian mobile: optional +91, 91 or 0, then 10 digits starting 6 to 9, often 5-5.
    Pattern(
        "MOBILE",
        re.compile(_NB + r"(?:(?:\+91|0091|91)[ -]?|0)?[6-9]\d{4}[ -]?\d{5}" + _NA),
    ),
    # Any bare run of 13 or more digits: too long to be an amount anyone writes unformatted,
    # and the length of card and account numbers.
    Pattern("ID_NUMBER", re.compile(_NB + r"\d(?:[ -]?\d){12,}" + _NA)),
    # Aadhaar: 12 digits, never starting 0 or 1, often written 4-4-4. No checksum test: a
    # number that fails Verhoeff is still a number someone meant to keep private.
    Pattern("AADHAAR", re.compile(_NB + r"[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}" + _NA)),
)
KINDS = tuple(p.kind for p in PATTERNS)


@dataclass(frozen=True)
class Redacted:
    text: str
    kinds: tuple[str, ...]  # each kind removed, once, in pattern order
    counts: dict[str, int]

    @property
    def changed(self) -> bool:
        return bool(self.kinds)


def _sub(p: Pattern, text: str, counts: Counter[str]) -> str:
    def replace(m: re.Match[str]) -> str:
        counts[p.kind] += 1
        start, end = m.span(p.group)
        whole_start = m.start()
        return m.group(0)[: start - whole_start] + f"<{p.kind}>" + m.group(0)[end - whole_start :]

    return p.regex.sub(replace, text)


def redact(text: str) -> Redacted:
    counts: Counter[str] = Counter()
    for p in PATTERNS:
        text = _sub(p, text, counts)
    kinds = tuple(k for k in KINDS if counts[k])
    return Redacted(text, kinds, dict(counts))


def found(text: str) -> list[tuple[str, str]]:
    """Every identifier still in ``text``, as (kind, the matched text). Empty means clean.

    The same pattern set as ``redact``, applied in the same order, so a text ``redact`` has
    already cleaned is always empty here."""
    hits: list[tuple[str, str]] = []
    for p in PATTERNS:
        for m in p.regex.finditer(text):
            hits.append((p.kind, m.group(p.group)))
        text = p.regex.sub(lambda m: f"<{p.kind}>", text)  # noqa: B023 - applied at once
    return hits


def notice(kinds: tuple[str, ...]) -> str | None:
    """The sentence the response carries when something was removed."""
    if not kinds:
        return None
    names = {
        "EMAIL": "an email address",
        "CIN": "a company identification number (CIN)",
        "GSTIN": "a GSTIN",
        "PAN": "a PAN",
        "IFSC": "an IFSC code",
        "ACCOUNT": "a bank account number",
        "AADHAAR": "an Aadhaar number",
        "MOBILE": "a mobile number",
        "ID_NUMBER": "a long identifying number",
    }
    listed = [names[k] for k in kinds]
    what = listed[0] if len(listed) == 1 else ", ".join(listed[:-1]) + " and " + listed[-1]
    return (
        f"Your question contained {what}. It was removed before the question was processed,"
        " and it was not stored, sent to any model or logged."
    )
