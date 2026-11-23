"""Answer level metrics. Each takes the row level results of one set on one split and one arm;
nothing here pools sets (the Phase 3 rule, unchanged).

- Citation validity is a lookup, not a judgement: an emitted citation either resolves to a
  real paragraph of the build or it does not. It is reported as a count of violations,
  because anything other than zero is a bug report, not a quality figure.
- Refusal correctness and the false refusal rate are one function returning both. Either
  alone rewards a degenerate system (refuse everything, answer everything), so the code
  makes it impossible to compute one without the other.
- Groundedness uses the judge, so it is never returned without the judge's agreement rate.
- Proportions carry a seeded percentile bootstrap interval, as in Phase 3; every figure
  carries its n and its count, since a rate on a few dozen questions hides how few events it
  describes.
"""

from __future__ import annotations

import statistics
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evals.metrics import Estimate, bootstrap_mean

Row = Mapping[str, Any]


def proportion(hits: Sequence[bool]) -> Estimate:
    return bootstrap_mean([1.0 if h else 0.0 for h in hits])


# --------------------------------------------------------------------------- citations


@dataclass(frozen=True)
class CitationCheck:
    citations: int
    violations: int  # citations that resolve to no paragraph of the build
    quote_violations: int  # citations whose recorded quote is not the cited text there
    examples: list[str]


def citation_validity(
    rows: Sequence[Row], paragraphs: Collection[str], bodies: Mapping[str, str]
) -> CitationCheck:
    """Resolve every emitted citation against the corpus. ``paragraphs`` is every para_id of
    the build; ``bodies`` the text of each cited one. No model is involved."""
    n = bad = bad_quote = 0
    examples: list[str] = []
    for r in rows:
        for claim in r.get("claims", []):
            quotes = claim.get("quotes", [])
            for cit in claim["citations"]:
                n += 1
                pid = cit["para_id"]
                if pid not in paragraphs:
                    bad += 1
                    examples.append(f"{r['qid']}: {pid} is not a paragraph of the build")
                    continue
                # Judged on the text, not on recorded offsets: a citation is a quote violation
                # when none of its claim's quotes occurs in the cited paragraph at all.
                body = bodies.get(pid, "")
                if not any(q and q in body for q in quotes):
                    bad_quote += 1
                    if len(examples) < 5:
                        examples.append(f"{r['qid']}: no quote of the claim is in {pid}")
    return CitationCheck(n, bad, bad_quote, examples)


# --------------------------------------------------------------------------- refusals


@dataclass(frozen=True)
class RefusalPair:
    """Refusal correctness on unanswerable questions and the false refusal rate on answerable
    ones, always together."""

    refused: int  # unanswerable questions refused
    unanswerable: int
    refused_for_expected_reason: int
    false_refusals: int  # answerable questions refused
    answerable: int
    correctness: Estimate
    false_refusal_rate: Estimate


def refusal_pair(answerable: Sequence[Row], unanswerable: Sequence[Row]) -> RefusalPair:
    if not answerable or not unanswerable:
        raise ValueError("the refusal pair needs both answerable and unanswerable questions")
    refused = [r["outcome"] == "refusal" for r in unanswerable]
    expected = [r["outcome"] == "refusal" and r["reason"] == r.get("expect") for r in unanswerable]
    wrongly = [r["outcome"] != "answer" for r in answerable]
    return RefusalPair(
        refused=sum(refused),
        unanswerable=len(unanswerable),
        refused_for_expected_reason=sum(expected),
        false_refusals=sum(wrongly),
        answerable=len(answerable),
        correctness=proportion(refused),
        false_refusal_rate=proportion(wrongly),
    )


# --------------------------------------------------------------------------- answers


@dataclass(frozen=True)
class GoldCheck:
    answered: int
    gold_cited: int  # answers citing the gold paragraph
    other_cited: int  # answers citing only other paragraphs: possibly wrong, never proven right
    wrong_version: int  # as_of set: answers citing a version not in force on the date asked
    accuracy: Estimate  # gold cited, over all questions in the set


def gold_check(rows: Sequence[Row]) -> GoldCheck:
    cited = [{c["para_id"] for cl in r.get("claims", []) for c in cl["citations"]} for r in rows]
    answered = [r["outcome"] == "answer" for r in rows]
    gold = [a and bool(set(r["gold"]) & c) for r, a, c in zip(rows, answered, cited, strict=True)]
    wrong = 0
    for r, a, c in zip(rows, answered, cited, strict=True):
        siblings = {p for p in c if p.split(":v")[0] == r["gold"][0].split(":v")[0]}
        same_anchor = {p for p in siblings if p.split("#")[1] == r["gold"][0].split("#")[1]}
        if a and same_anchor - set(r["gold"]):
            wrong += 1
    return GoldCheck(
        answered=sum(answered),
        gold_cited=sum(gold),
        other_cited=sum(answered) - sum(gold),
        wrong_version=wrong,
        accuracy=proportion(gold),
    )


def recall_at(rows: Sequence[Row], k: int) -> Estimate:
    """Retrieval beside the answers: is the gold paragraph, that exact version, among the
    first k ranked candidates this arm retrieved for the question."""
    return proportion([bool(set(r["gold"]) & set(r["ranked"][:k])) for r in rows])


def _family(para_id: str) -> str:
    """The same numbered paragraph in any version of its direction."""
    doc, anchor = para_id.split("#", 1)
    return doc.rsplit(":v", 1)[0] + "#" + anchor


def recall_any_version_at(rows: Sequence[Row], k: int) -> Estimate:
    """As ``recall_at``, but any version of the gold paragraph counts: the recall a system
    without a notion of "in force on the date" would report for itself."""
    return proportion(
        [
            bool({_family(g) for g in r["gold"]} & {_family(p) for p in r["ranked"][:k]})
            for r in rows
        ]
    )


# --------------------------------------------------------------------------- the judge


@dataclass(frozen=True)
class Agreement:
    agree: int
    n: int
    rate: Estimate
    kappa: float | None


def agreement(pairs: Sequence[tuple[bool, bool]]) -> Agreement:
    """Judge against person, over labelled pairs: (judge said supported, person said
    supported). Raw agreement with its interval, and Cohen's kappa beside it."""
    n = len(pairs)
    same = [j == h for j, h in pairs]
    kappa = None
    if n:
        po = sum(same) / n
        pj = sum(j for j, _ in pairs) / n
        ph = sum(h for _, h in pairs) / n
        pe = pj * ph + (1 - pj) * (1 - ph)
        kappa = None if pe == 1 else (po - pe) / (1 - pe)
    return Agreement(sum(same), n, proportion(same), kappa)


@dataclass(frozen=True)
class Groundedness:
    supported: int
    judged: int
    rate: Estimate
    judge_agreement: Agreement | None  # None until the hand labels are in; then it is shown


def groundedness(verdicts: Sequence[bool], judge_agreement: Agreement | None) -> Groundedness:
    """Claims the judge found supported by their cited paragraphs, carried with the judge's
    own agreement rate so the two are never separated."""
    return Groundedness(sum(verdicts), len(verdicts), proportion(verdicts), judge_agreement)


# --------------------------------------------------------------------------- cost


@dataclass(frozen=True)
class Spread:
    p50: float | None
    p95: float | None
    n: int


def spread(values: Sequence[float]) -> Spread:
    v = sorted(values)
    if not v:
        return Spread(None, None, 0)
    if len(v) == 1:
        return Spread(v[0], v[0], 1)
    q = statistics.quantiles(v, n=100, method="inclusive")
    return Spread(statistics.median(v), q[94], len(v))
