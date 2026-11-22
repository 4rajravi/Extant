"""Paragraph anchors for RBI directions: the part of a para_id after ``#``.

Decided 19 November 2026. RBI numbers a direction's paragraphs continuously through the whole
document; no direction in the corpus restarts numbering per chapter. So:

``p<number>``
    A paragraph, by the number printed on it: ``62A.`` is ``p62A``, ``4.1`` is ``p4.1``. A space
    RBI sometimes prints before an inserted letter (``38 A.``) is dropped, giving ``p38A``.
``annex<n>``, ``annex<n>.p<number>``
    An annex printed inside the page, numbered as an arabic numeral (``Annex III`` is ``annex3``),
    and a paragraph numbered inside it. A schedule or an appendix keeps its own word
    (``SCHEDULE-I`` is ``schedule1``). An unnumbered single annex is ``annex``.
``preamble``
    Text before the first numbered paragraph.

A paragraph includes its sub-paragraphs, provisos, lists and tables, so numbered notes under a
table belong to the paragraph that holds the table and never become anchors of their own.
"""

from __future__ import annotations

import re

from extant.core.identity import ANCHOR_RE, MAX_ANCHOR_LENGTH, InvalidIdentifierError

PREAMBLE = "preamble"

_PRINTED = re.compile(r"\d+[A-Z]{0,3}(?:\.\d+[A-Z]{0,3})*")
_ANNEX_LABEL = re.compile(
    # the number must be set apart from the word, so "Annexures" (a group heading) is no annex
    r"^(?P<kind>annexure|annex|appendix|schedule)"
    r"(?:(?:\s*[-\u2013\u2014.:]\s*|\s+)(?P<number>[IVXLC]+|\d+|[A-Z]))?\s*$",
    re.IGNORECASE,
)
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_VALID_ROMAN = re.compile(r"C{0,3}(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")


def _checked(anchor: str) -> str:
    if len(anchor) > MAX_ANCHOR_LENGTH or ANCHOR_RE.fullmatch(anchor) is None:
        raise InvalidIdentifierError(f"not a valid paragraph anchor: {anchor!r}")
    return anchor


def printed_number(text: str) -> str:
    """Normalise a printed paragraph number: drop the trailing dot and a space before an
    inserted letter. ``'62A.'`` and ``'62 A.'`` are ``'62A'``; ``'4.1'`` stays ``'4.1'``."""
    s = re.sub(r"(?<=\d)\s+(?=[A-Z]\b)", "", text.strip()).rstrip(".")
    if _PRINTED.fullmatch(s) is None:
        raise InvalidIdentifierError(f"not a printed paragraph number: {text!r}")
    return s


def paragraph_anchor(printed: str) -> str:
    return _checked(f"p{printed_number(printed)}")


def _roman(numeral: str) -> int:
    values = [_ROMAN[ch] for ch in numeral.upper()]
    total = sum(
        -v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values)
    )
    if total < 1:
        raise InvalidIdentifierError(f"not a roman numeral: {numeral!r}")
    return total


def annex_anchor(label: str, paragraph: str | None = None) -> str:
    """``'Annex III'`` is ``annex3``; with paragraph ``'2.'`` it is ``annex3.p2``. Raises on a
    label that is not an annex, schedule or appendix heading."""
    m = _ANNEX_LABEL.fullmatch(label.strip())
    if m is None:
        raise InvalidIdentifierError(f"not an annex heading: {label!r}")
    kind = m["kind"].lower()
    kind = "annex" if kind == "annexure" else kind
    number = m["number"]
    if number is None:
        suffix = ""
    elif number.isdigit():
        suffix = str(int(number))
    elif _VALID_ROMAN.fullmatch(number.upper()) and (
        len(number) > 1 or number.upper() in {"I", "V", "X"}
    ):
        suffix = str(_roman(number))
    elif len(number) == 1:
        suffix = number.upper()  # a lettered annex: "Annex A" is annexA
    else:
        raise InvalidIdentifierError(f"not a roman numeral or a single letter: {label!r}")
    base = f"{kind}{suffix}"
    if paragraph is None:
        return _checked(base)
    return _checked(f"{base}.p{printed_number(paragraph)}")
