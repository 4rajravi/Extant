"""Retrieval metrics: the one place a metric is defined.

Four metrics in Phase 3:

- recall@k: the share of queries with at least one gold item in the top k, k in ``DEPTHS``.
- MRR: the mean reciprocal rank of the first gold item (0 when none is returned).
- Entity filter accuracy: the share of returned paragraphs whose entity scope includes a
  queried entity.
- As-of accuracy: the share of returned paragraphs in force on the as-of date.

The last two are invariants rather than scores. Anything below 1.0 is a bug in the prefilter,
so each is reported as a count of violations beside the ratio: 0.998 reads as fine, and
twelve leaked paragraphs does not.

Recall is computed at paragraph level and at document level, always as separate numbers.
Where a set's labels reach only the document, paragraph recall has no queries and is
reported as absent, never filled in from the document figure.

Every recall and MRR figure carries its n and a bootstrap confidence interval.

Nothing here pools sets. ``summarise`` takes one ``SetRows``, and ``SetRows`` refuses rows
from more than one set, arm, split or corpus. There is deliberately no function that takes
two sets, so a pooled number has to be written by hand to exist.

The answer level metrics belong to Phase 6, when there is an agent to produce answers. Their
signatures are settled here and their bodies raise.
"""

from __future__ import annotations

import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

DEPTHS = (1, 5, 10, 20)
BOOTSTRAP_RESAMPLES = 2_000
BOOTSTRAP_SEED = 20261120
CONFIDENCE = 0.95

# --------------------------------------------------------------------------- per query


def first_hit_rank(returned: Sequence[str], gold: Collection[str]) -> int | None:
    """1-based rank of the first returned item that is gold; None if none is, or if there
    is no gold to find."""
    if not gold:
        return None
    for rank, item in enumerate(returned, start=1):
        if item in gold:
            return rank
    return None


def hit_at(rank: int | None, k: int) -> bool:
    return rank is not None and rank <= k


def reciprocal_rank(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / rank


def entity_violations(returned_codes: Sequence[Sequence[str]], queried: Collection[str]) -> int:
    """Returned paragraphs whose entity scope shares no code with the query's."""
    wanted = set(queried)
    return sum(1 for codes in returned_codes if not wanted & set(codes))


def as_of_violations(windows: Sequence[tuple[date, date | None]], as_of: date) -> int:
    """Returned paragraphs not in force on ``as_of``: ``[valid_from, valid_to)``."""
    return sum(1 for start, end in windows if not (start <= as_of and (end is None or end > as_of)))


# --------------------------------------------------------------------------- uncertainty


@dataclass(frozen=True)
class Estimate:
    value: float | None  # None when n is 0: there is nothing to estimate
    low: float | None
    high: float | None
    n: int

    def overlaps(self, other: Estimate) -> bool:
        a_low, a_high, b_low, b_high = self.low, self.high, other.low, other.high
        if a_low is None or a_high is None or b_low is None or b_high is None:
            return True  # nothing to compare is never evidence of a difference
        return a_low <= b_high and b_low <= a_high


def bootstrap_mean(
    values: Sequence[float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    confidence: float = CONFIDENCE,
) -> Estimate:
    """The mean with a percentile bootstrap interval. Seeded, so the same values give the
    same interval and a committed table does not churn."""
    n = len(values)
    if n == 0:
        return Estimate(None, None, None, 0)
    mean = sum(values) / n
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    tail = (1.0 - confidence) / 2.0
    low = means[int(tail * resamples)]
    high = means[min(resamples - 1, int((1.0 - tail) * resamples))]
    return Estimate(mean, low, high, n)


# --------------------------------------------------------------------------- one set, never two


class PoolingError(ValueError):
    """Rows from more than one set, arm, split or corpus were passed as one."""


KEYS = ("set", "arm", "split", "corpus")


class SetRows:
    """Result rows for exactly one set, arm, split and corpus. The only input ``summarise``
    accepts, which is what makes a pooled number awkward to express."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            raise ValueError("no rows")
        for key in KEYS:
            values = {r[key] for r in rows}
            if len(values) != 1:
                raise PoolingError(
                    f"rows span {len(values)} values of {key!r} ({sorted(map(str, values))});"
                    " metrics are never pooled: summarise each one separately"
                )
        self.rows = list(rows)
        self.key = {k: rows[0][k] for k in KEYS}

    def subset(self, *, holdout: bool | None) -> list[Mapping[str, Any]]:
        if holdout is None:
            return self.rows
        return [r for r in self.rows if bool(r["holdout"]) is holdout]


def summarise(rows: SetRows, *, holdout: bool | None = None) -> dict[str, Any]:
    """Every Phase 3 metric for one set, arm, split and corpus, from the per query rows.
    ``holdout`` narrows to the memorisation holdout (True) or the rest (False)."""
    subset = rows.subset(holdout=holdout)
    para = [r for r in subset if r["gold_para_ids"]]
    out: dict[str, Any] = {**rows.key, "bucket": _bucket(holdout), "n": len(subset)}
    out["n_paragraph_gold"] = len(para)
    for level, items, rank_key in (
        ("para", para, "para_rank"),
        ("doc", subset, "doc_rank"),
        ("para_ctx", para, "para_rank_with_context"),
    ):
        for k in DEPTHS:
            est = bootstrap_mean([1.0 if hit_at(r[rank_key], k) else 0.0 for r in items])
            out.update(_flatten(f"recall@{k}_{level}", est))
        mrr = bootstrap_mean([reciprocal_rank(r[rank_key]) for r in items])
        out.update(_flatten(f"mrr_{level}", mrr))
    for name in ("entity", "as_of"):
        returned = sum(int(r["returned_count"]) for r in subset)
        bad = sum(int(r[f"{name}_violations"]) for r in subset)
        out[f"{name}_violations"] = bad
        out[f"{name}_returned"] = returned
        out[f"{name}_accuracy"] = None if returned == 0 else (returned - bad) / returned
    return out


def _bucket(holdout: bool | None) -> str:
    return "all" if holdout is None else ("holdout" if holdout else "main")


def _flatten(name: str, est: Estimate) -> dict[str, Any]:
    return {name: est.value, f"{name}_low": est.low, f"{name}_high": est.high, f"{name}_n": est.n}


def estimate_of(summary: Mapping[str, Any], name: str) -> Estimate:
    return Estimate(
        summary[name], summary[f"{name}_low"], summary[f"{name}_high"], summary[f"{name}_n"]
    )


# --------------------------------------------------------------------------- label quality


def audited_precision(verdicts: Sequence[str | None]) -> float | None:
    """The miner's precision from a person's verdicts: None until every pair is judged."""
    if not verdicts or any(v is None for v in verdicts):
        return None
    judged = [v for v in verdicts if v is not None]
    return sum(1 for v in judged if v == "correct") / len(judged)


# --------------------------------------------------------------------------- Phase 6


def citation_validity(answer: Any, retrieved: Any) -> float:
    """Share of an answer's citations that resolve to a returned paragraph saying what the
    citation claims. Needs the agent: Phase 6."""
    raise NotImplementedError("citation validity is an answer level metric: Phase 6")


def groundedness(answer: Any, retrieved: Any) -> float:
    """Share of an answer's claims supported by the paragraphs it cites. Phase 6."""
    raise NotImplementedError("groundedness is an answer level metric: Phase 6")


def refusal_correctness(answers: Sequence[Any], should_refuse: Sequence[bool]) -> float:
    """Share of questions the system should refuse that it did refuse. Phase 6."""
    raise NotImplementedError("refusal correctness is an answer level metric: Phase 6")


def false_refusal_rate(answers: Sequence[Any], should_refuse: Sequence[bool]) -> float:
    """Share of answerable questions the system refused. Phase 6."""
    raise NotImplementedError("false refusal rate is an answer level metric: Phase 6")


def cost_per_question(usage: Sequence[Any]) -> float:
    """Mean provider cost of answering one question, in EUR. Phase 6."""
    raise NotImplementedError("cost per question needs the agent: Phase 6")
