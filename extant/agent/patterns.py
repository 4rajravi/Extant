"""Deterministic signals in the question, checked before any model is called.

These decide the first two routes without a model where the text alone settles it, which is
what lets a question carrying a literal reference take the fast path with zero model calls
and still exit when it is out of scope: precedence is out of scope, then computation, then a
reference. The model's own scope verdict covers every question that reaches it; these
patterns are deliberately narrow, and the full scope middleware is Phase 5.
"""

from __future__ import annotations

import re

# Other regulators' and other laws' subject matter, which no RBI direction answers.
_OUT_OF_SCOPE = re.compile(
    r"\b(?:GST|goods and services tax|income[- ]tax|capital gains tax|TDS rate|SEBI|IRDAI|"
    r"PFRDA|stock (?:tip|price|recommendation)|share price|mutual fund NAV|insurance premium|"
    r"weather|cricket|recipe|horoscope)\b",
    re.IGNORECASE,
)

# Asking the system to do arithmetic on the caller's own figures. Only arithmetic: a request
# for a choice or a permission ("should we", "recommend whether") is a question about a rule
# in natural phrasing, answered with the not_legal_advice caveat, never refused. Getting
# these two the wrong way round would refuse a compliance officer's ordinary question.
_COMPUTATION = re.compile(
    r"\b(?:calculate|compute|work out|"
    r"how much (?:tax|interest|capital|penalty|provision) (?:do|will|would|should|must) "
    r"(?:i|we|my|our)|"
    r"how much (?:do|does|will|would|should|must) (?:i|we|my bank|our bank|my company|our "
    r"company) (?:owe|pay|have to pay|need to (?:hold|keep|provide|set aside))|"
    r"what (?:is|would be|will be) (?:my|our) (?:crar|ratio|provision|liability|penalty|"
    r"interest))\b",
    re.IGNORECASE,
)

# Phrased as a request for permission or advice: answered, with the not_legal_advice caveat.
_ADVICE = re.compile(
    r"\b(?:should (?:i|we)|can (?:i|we)|may (?:i|we)|am i allowed|are we allowed|"
    r"is it (?:legal|permissible|allowed|ok|okay) for (?:me|us|my|our)|"
    r"(?:advise|recommend)(?: (?:me|us))? (?:on|whether|how|which)|get away with)\b",
    re.IGNORECASE,
)


def out_of_scope(question: str) -> str | None:
    """The phrase that puts the question out of scope, or None."""
    m = _OUT_OF_SCOPE.search(question)
    return m.group(0) if m else None


def computation_requested(question: str) -> str | None:
    m = _COMPUTATION.search(question)
    return m.group(0) if m else None


def advice_phrasing(question: str) -> bool:
    return _ADVICE.search(question) is not None
