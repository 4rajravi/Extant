"""The numeric check: every number in a claim appears in one of its cited quotes.

A digit scan alone would refuse correct answers, so both sides are normalised first:

- digits with Indian or Western grouping ("1,00,000", "100,000") and decimals;
- number words up to the hundreds ("thirty", "twenty-five", "one hundred and eighty");
- percent spellings ("10%", "10 per cent", "10 percent", "10 pc") and basis points;
- periods ("30 days", "thirty days", "6 months", "one year"), compared as value and unit;
- lakh and crore folded into the value ("Rs 5 lakh" equals "Rs 5,00,000");
- dates in ISO, "January 1, 2026", "1 January 2026" and "01.01.2026" forms, compared as dates.

A number with a unit matches the same value with the same unit, or the same value with no
unit on either side; "10" in a claim matches "10 per cent" in a quote. Numbers that name
rather than state are not checked: paragraph, section, chapter and annex numbers, anything
inside a circular reference, and numbers in the title or paragraph number of a cited
paragraph ("the ... Directions, 2025").

An unmatched number is a hard failure, and every one is logged with both strings, so the
Phase 6 false refusal rate can tell a real catch from a normalisation gap.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

log = logging.getLogger("extant.agent.numerics")

_UNITS_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))

_DATE_PATTERNS = (
    re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b"),
    re.compile(
        rf"\b(?P<mon>{_MONTH_RE})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>\d{{4}})\b", re.I
    ),
    re.compile(
        rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<mon>{_MONTH_RE})\.?,?\s+(?P<y>\d{{4}})\b", re.I
    ),
    re.compile(r"\b(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>\d{4})\b"),
)
# Numbers that name a provision or an instrument rather than state a quantity.
_NAMING = re.compile(
    r"\b(?:para(?:graph)?s?|sub-?para(?:graph)?s?|sections?|sub-?sections?|clauses?|"
    r"sub-?clauses?|chapters?|annex(?:ure)?s?|appendix|appendices|rules?|regulations?|"
    r"schedules?|items?|serial|s\.? ?no\.?|no\.)\s*[\dA-Za-z().]+(?:\s*(?:and|to|,|-)\s*"
    r"[\dA-Za-z().]+)*",
    re.IGNORECASE,
)
_REFERENCE = re.compile(r"\S*[A-Za-z]\S*/\S+")  # circular references contain a slash
_WORD_NUMBER = re.compile(
    r"\b(?:(?:"
    + "|".join(_TENS_WORDS)
    + r")(?:[- ](?:"
    + "|".join(_UNITS_WORDS)
    + r"))?|"
    + "|".join(_UNITS_WORDS)
    + r")(?:\s+hundred(?:\s+(?:and\s+)?(?:(?:"
    + "|".join(_TENS_WORDS)
    + r")(?:[- ](?:"
    + "|".join(_UNITS_WORDS)
    + r"))?|"
    + "|".join(_UNITS_WORDS)
    + r"))?)?\b",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_UNIT_AFTER = re.compile(
    r"^\s*(?P<unit>%|per\s*-?\s*cent\b|percent\b|pc\b|basis points?\b|bps?\b|"
    r"days?\b|weeks?\b|months?\b|years?\b|lakhs?\b|crores?\b|lacs?\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, order=True)
class Quantity:
    value: Decimal
    unit: str | None  # "%", "bp", "day", "week", "month", "year", "date", or None

    def __str__(self) -> str:
        if self.unit == "date":
            return date.fromordinal(int(self.value)).isoformat()
        return f"{self.value.normalize():f}{'' if self.unit is None else ' ' + self.unit}"


def _word_value(text: str) -> int:
    words = re.split(r"[\s-]+", text.lower().replace(" and ", " "))
    total, current = 0, 0
    for w in words:
        if w in _UNITS_WORDS:
            current += _UNITS_WORDS[w]
        elif w in _TENS_WORDS:
            current += _TENS_WORDS[w]
        elif w == "hundred":
            current = (current or 1) * 100
    return total + current


def _unit(after: str) -> tuple[str | None, Decimal]:
    """The unit right after a number, and the multiplier it implies."""
    m = _UNIT_AFTER.match(after)
    if not m:
        return None, Decimal(1)
    u = m.group("unit").lower().replace(" ", "").replace("-", "")
    if u in {"%", "percent", "pc"} or u.startswith("per"):
        return "%", Decimal(1)
    if u.startswith(("basis", "bp")):
        return "bp", Decimal(1)
    if u.startswith(("lakh", "lac")):
        return None, Decimal(100_000)
    if u.startswith("crore"):
        return None, Decimal(10_000_000)
    # Periods compare across spellings: a year is twelve months, a week seven days. Months
    # and days are not converted into each other, since a month is not a fixed number of days.
    if u.startswith("year"):
        return "month", Decimal(12)
    if u.startswith("week"):
        return "day", Decimal(7)
    return u.rstrip("s"), Decimal(1)


def quantities(text: str, *, ignore: Iterable[str] = ()) -> list[Quantity]:
    """Every quantity stated in ``text``, normalised. ``ignore`` is text whose numbers name
    things (a document title, a paragraph number) and are not checked."""
    s = " ".join(text.split())
    for phrase in ignore:
        # Whole phrases only, and only ones long enough to be a title: ignoring a bare "1"
        # as a substring once turned "12 per cent" into "2 per cent".
        flat = " ".join(phrase.split())
        if len(flat) >= 8:
            s = re.sub(rf"(?<!\w){re.escape(flat)}(?!\w)", " " * len(flat), s)
    out: list[Quantity] = []

    def blank(m: re.Match[str]) -> str:
        return " " * (m.end() - m.start())

    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(s):
            g = m.groupdict()
            month = _MONTHS[g["mon"].lower()] if g.get("mon") else int(g["m"])
            try:
                d = date(int(g["y"]), month, int(g["d"]))
            except ValueError:
                continue
            out.append(Quantity(Decimal(d.toordinal()), "date"))
        s = pattern.sub(blank, s)
    s = _REFERENCE.sub(blank, s)
    s = _NAMING.sub(blank, s)
    for m in _WORD_NUMBER.finditer(s):
        unit, mult = _unit(s[m.end() :])
        out.append(Quantity(Decimal(_word_value(m.group(0))) * mult, unit))
    s = _WORD_NUMBER.sub(blank, s)
    for m in _DIGITS.finditer(s):
        try:
            value = Decimal(m.group(0).replace(",", ""))
        except InvalidOperation:
            continue
        unit, mult = _unit(s[m.end() :])
        out.append(Quantity(value * mult, unit))
    return out


def _matches(claim: Quantity, quoted: Sequence[Quantity]) -> bool:
    for q in quoted:
        if q.value != claim.value:
            continue
        if claim.unit == q.unit or claim.unit is None or q.unit is None:
            if "date" in (claim.unit, q.unit) and claim.unit != q.unit:
                continue  # a date never matches a bare number
            return True
    return False


@dataclass(frozen=True)
class NumericResult:
    passed: bool
    unmatched: tuple[Quantity, ...]
    claim: tuple[Quantity, ...]
    quoted: tuple[Quantity, ...]


def check(claim_text: str, quotes: Sequence[str], *, ignore: Iterable[str] = ()) -> NumericResult:
    ignored = list(ignore)
    in_claim = quantities(claim_text, ignore=ignored)
    in_quotes = [q for quote in quotes for q in quantities(quote)]
    unmatched = tuple(q for q in in_claim if not _matches(q, in_quotes))
    if unmatched:
        log.warning(
            "numeric check failed: %s not in quotes | claim=%r | quotes=%r",
            ", ".join(map(str, unmatched)),
            claim_text,
            list(quotes),
        )
    return NumericResult(not unmatched, unmatched, tuple(in_claim), tuple(in_quotes))
