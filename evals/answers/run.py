"""The answer level runner: every question of every set through the real service, per arm.

    uv run python -m evals.answers.run plan                   # the dry run: no calls at all
    uv run python -m evals.answers.run ask --run 2026-11-23   # the real run; resumable
    uv run python -m evals.answers.run judge --run 2026-11-23 # the judge over every answered claim

**Cassettes.** Every model call goes through a cassette keyed on the exact call (schema,
system prompt, user message). A call already in the cassette is replayed and costs nothing;
a new one goes to the real model through the middleware and is written down with the model
id and the date. So an arm that asks a model nothing new (the arm without verification)
costs nothing, a resumed run never pays twice for a call, and a stale cassette is visible by
its date. Query embeddings are cassetted the same way. No gold answer is in any cassette:
the composer only ever sees the question and the retrieved paragraphs.

**Resumable.** Each question's row is appended to ``runs/<run>/rows.jsonl`` as it finishes;
a restarted run skips every (arm, question) already there.

**Spend.** The dry run's estimate is written beside the actual spend (fresh calls only, at
the provider's reported tokens) in ``runs/<run>/summary.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from evals.answers.sets import QUESTIONS, REMOVED, Question, _build_schema
from extant.agent.arms import BASELINE, Arm
from extant.agent.llm import Completion, StructuredModel, call_key
from extant.agent.models import Answer, JudgeOutput, Refusal, RetrievalOnly
from extant.agent.pricing import chat_eur, embedding_eur
from extant.index.embed import Embedded, Embedder, Kind

T = TypeVar("T", bound=BaseModel)

ROOT = Path(__file__).resolve().parent
CASSETTES = ROOT / "cassettes"
RUNS = ROOT / "runs"
FIXTURES = ROOT.parents[1] / "tests" / "fixtures" / "agent"
EVAL_TODAY = date(2026, 11, 23)  # what "today" means to every question of a run
CEILING_EUR = 15.0  # the monthly model budget the spend is judged against
# This run's hard stop: no new question starts once it could pass this. Approved at EUR 6.60
# in all (23 November 2026), leaving room for the judge pass that follows (about EUR 0.03).
BUDGET_EUR = 6.55

# In run order: the baseline first (later arms replay its calls), the cheap arms next, the
# costly one last, so a budget stop trims the arm that can best spare it.
ARMS: dict[str, Arm] = {
    "baseline": BASELINE,
    "no_verification": Arm("no_verification", verification=False),
    "retry": Arm("retry", retry=True),
    "no_supersession": Arm("no_supersession", supersession=False),
}


# --------------------------------------------------------------------------- cassettes


class InfrastructureError(RuntimeError):
    """Postgres or a model was unavailable: the run stops rather than score an outage."""


class CassetteMissError(LookupError):
    """A replay asked a model something no cassette holds, and no real model was given."""


class CassetteStore:
    """Append-only JSONL of model calls, loaded into memory, keyed on the exact call."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line:
                    e = json.loads(line)
                    self.entries[e["key"]] = e

    def get(self, key: str) -> dict[str, Any] | None:
        return self.entries.get(key)

    def put(self, entry: dict[str, Any]) -> None:
        self.entries[entry["key"]] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


@dataclass
class CassetteModel:
    """Replays a recorded call, or makes it (through ``inner``, the guarded real model) and
    records it with the model id and the date. ``inner=None`` is replay only."""

    name: str
    store: CassetteStore
    inner: StructuredModel | None
    eur_per_usd: float = 0.90
    recorded_on: date = EVAL_TODAY
    fresh: int = 0
    replayed: int = 0
    spent_eur: float = 0.0

    @property
    def model(self) -> str:
        return self.name

    def complete(self, schema: type[T], *, system: str, user: str) -> Completion:
        key = call_key(schema, system, user)
        e = self.store.get(key)
        if e is not None:
            self.replayed += 1
            return Completion(
                parsed=schema.model_validate(e["parsed"]),
                model=e["model"],
                input_tokens=int(e["input_tokens"]),
                output_tokens=int(e["output_tokens"]),
                latency_ms=float(e["latency_ms"]),
            )
        if self.inner is None:
            raise CassetteMissError(f"no cassette for this {schema.__name__} call ({key[:12]})")
        done = self.inner.complete(schema, system=system, user=user)
        self.fresh += 1
        self.spent_eur += chat_eur(
            done.model, done.input_tokens, done.output_tokens, self.eur_per_usd
        )
        self.store.put(
            {
                "key": key,
                "schema": schema.__name__,
                "model": done.model,
                "recorded_on": self.recorded_on.isoformat(),
                "input_tokens": done.input_tokens,
                "output_tokens": done.output_tokens,
                "latency_ms": round(done.latency_ms, 1),
                "prompt_sha256": hashlib.sha256((system + "\n" + user).encode()).hexdigest(),
                "parsed": done.parsed.model_dump(mode="json"),
                # The judge's prompt is small and is what a hand label is about; the others
                # carry corpus paragraphs and are identified by their hash.
                "user": user if schema is JudgeOutput else None,
            }
        )
        return done


@dataclass
class CachedEmbedder:
    """Query embeddings, cassetted like model calls."""

    inner: Embedder | None
    store: CassetteStore
    eur_per_usd: float = 0.90
    fresh: int = 0
    spent_eur: float = 0.0
    identity: Any = None

    def __post_init__(self) -> None:
        if self.identity is None and self.inner is not None:
            self.identity = self.inner.identity

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> Embedded:
        vectors, tokens = [], 0
        for text in texts:
            key = hashlib.sha256(f"{self.identity.model_id}|{kind}|{text}".encode()).hexdigest()
            e = self.store.get(key)
            if e is None:
                if self.inner is None:
                    raise CassetteMissError(f"no cassetted embedding for {text[:40]!r}")
                done = self.inner.embed([text], kind=kind)
                self.fresh += 1
                tokens += done.tokens
                self.spent_eur += embedding_eur(
                    self.identity.model_id, done.tokens, self.eur_per_usd
                )
                e = {"key": key, "vector": done.vectors[0], "recorded_on": EVAL_TODAY.isoformat()}
                self.store.put(e)
            vectors.append(e["vector"])
        return Embedded(vectors, tokens)


# --------------------------------------------------------------------------- the plan


@dataclass(frozen=True)
class Planned:
    arm: str
    q: Question


# Which questions each arm is asked: every arm, every question (approved at EUR 6.60 on
# 23 November 2026). The no-verification arm replays the baseline's calls and the retry arm pays
# only for a retried composer call; the arm without supersession handling needs a new composer
# call almost everywhere, which is most of the cost.
SCOPE: dict[str, Callable[[Question], bool]] = {
    "baseline": lambda q: True,
    "no_verification": lambda q: True,
    "retry": lambda q: True,
    "no_supersession": lambda q: True,
}
SCOPE_TEXT = dict.fromkeys(SCOPE, "every question")


def plan(arms: Iterable[str], splits: Iterable[str]) -> list[Planned]:
    """Every arm's questions, arm by arm, the baseline first so later arms replay its calls."""
    wanted = set(splits)
    return [
        Planned(a, q)
        for a in arms
        for q in QUESTIONS
        if q.split in wanted and SCOPE[a](q) and q.qid not in REMOVED
    ]


@dataclass(frozen=True)
class CostModel:
    """Per call costs measured on the Phase 4 and 5 recordings, in EUR."""

    parse: float
    compose: float
    judge_call: float
    n_compose: int

    @classmethod
    def from_fixtures(cls, fixtures: Path = FIXTURES) -> CostModel:
        rows: dict[str, list[float]] = {"parse": [], "compose": [], "judge": []}
        for path in fixtures.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("derived_from") or not data.get("recorded"):
                continue
            for n in data["recorded"]["node_log"]:
                if n["node"] in rows and n["calls"]:
                    rows[n["node"]].append(n["eur"] / n["calls"])
        return cls(
            parse=statistics.mean(rows["parse"]),
            compose=statistics.mean(rows["compose"]),
            judge_call=statistics.mean(rows["judge"]),
            n_compose=len(rows["compose"]),
        )


def _fresh_calls(p: Planned) -> dict[str, float]:
    """Expected fresh model calls for one planned question, after cassette reuse across arms
    (the baseline is run first, so a later arm pays only for calls the baseline did not make).
    Fractions are expectations: a judge on about one claim in four, a retry on about one
    answer in ten, as measured in Phases 4 and 5."""
    q = p.q
    if q.source == "withdrawn":
        return {}  # the fast path: no model
    composes = q.source != "before"  # before the instrument existed: the gate refuses first
    if p.arm == "baseline":
        return {
            "parse": 1,
            "compose": 1 if composes else 0,
            "judge": 0.25 if q.set != "unanswerable" else 0,
        }
    if p.arm == "no_supersession":
        return {"compose": 1, "judge": 0.25}  # new candidates mean a new composer prompt
    if p.arm == "retry":
        return {"compose": 0.10 if q.set != "unanswerable" else 0}
    return {}  # no_verification: the baseline's calls, replayed


@dataclass(frozen=True)
class Estimate:
    questions: int
    by_arm: dict[str, int]
    calls: dict[str, float]
    eur: float
    judge_pass_eur: float
    embeddings_eur: float
    cost_model: CostModel

    @property
    def total(self) -> float:
        return self.eur + self.judge_pass_eur + self.embeddings_eur


def expected_eur(p: Planned, cost: CostModel) -> float:
    calls = _fresh_calls(p)
    return (
        calls.get("parse", 0) * cost.parse
        + calls.get("compose", 0) * cost.compose
        + calls.get("judge", 0) * cost.judge_call
    )


def estimate(planned: Sequence[Planned], cost: CostModel) -> Estimate:
    calls: dict[str, float] = {"parse": 0.0, "compose": 0.0, "judge": 0.0}
    for p in planned:
        for k, v in _fresh_calls(p).items():
            calls[k] += v
    eur = calls["parse"] * cost.parse + calls["compose"] * cost.compose
    eur += calls["judge"] * cost.judge_call
    answers = sum(1 for p in planned if p.q.set != "unanswerable")
    # The judge pass: every answered claim (about four per answer), new ones only.
    unique_answers = sum(
        1 for p in planned if p.q.set != "unanswerable" and p.arm in ("baseline", "no_supersession")
    )
    judge_pass = unique_answers * 4 * cost.judge_call
    texts = len({(p.q.text, p.q.entity) for p in planned})
    embeddings = embedding_eur("openai/text-embedding-3-large", texts * 30, 0.90)
    by_arm: dict[str, int] = {}
    for p in planned:
        by_arm[p.arm] = by_arm.get(p.arm, 0) + 1
    _ = answers
    return Estimate(len(planned), by_arm, calls, eur, judge_pass, embeddings, cost)


def show(est: Estimate, splits: Sequence[str]) -> str:
    c = est.cost_model
    lines = [
        f"questions: {len({p for p in QUESTIONS if p.split in splits})} on {', '.join(splits)};"
        f" planned (arm x question): {est.questions}",
        "by arm: " + ", ".join(f"{a} {n} ({SCOPE_TEXT[a]})" for a, n in est.by_arm.items()),
        f"cost model (from {c.n_compose} recorded composer calls): parse EUR {c.parse:.5f},"
        f" compose EUR {c.compose:.4f}, judge EUR {c.judge_call:.5f} per call",
        "expected fresh calls: "
        + ", ".join(f"{k} {v:.0f}" for k, v in est.calls.items())
        + " (the no-verification arm replays the baseline's calls)",
        f"estimate: arms EUR {est.eur:.2f} + judge pass EUR {est.judge_pass_eur:.2f}"
        f" + embeddings EUR {est.embeddings_eur:.4f} = EUR {est.total:.2f}"
        f" ({est.total / CEILING_EUR:.0%} of the EUR {CEILING_EUR:.0f} monthly ceiling)",
        f"hard stop: no question starts once spend could pass EUR {BUDGET_EUR:.2f}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- one question


def _ranked(state: Any, arm: Arm) -> list[str]:
    pool = [*state.candidates, *state.set_aside]
    primary = {"ranked", "reference"} if arm.supersession else {"probe", "reference"}
    seen: dict[str, int] = {}
    for c in pool:
        if c.rank is not None and c.origin in primary:
            seen.setdefault(c.para_id, c.rank)
    return [p for p, _ in sorted(seen.items(), key=lambda kv: kv[1])]


def row_for(planned: Planned, served: Any, run: str, spent: float, fresh: int) -> dict[str, Any]:
    q, response, state = planned.q, served.response, served.state
    result = response.result
    claims = []
    if isinstance(result, Answer) and state is not None:
        drafts = list(state.draft_claims)
        for i, claim in enumerate(result.claims):
            claims.append(
                {
                    "text": claim.text,
                    "citations": [
                        {
                            "para_id": c.para_id,
                            "quote_start": c.quote_start,
                            "quote_end": c.quote_end,
                        }
                        for c in claim.citations
                    ],
                    "quotes": list(drafts[i].quotes) if i < len(drafts) else [],
                }
            )
    checks = state.checks if state is not None else {}
    return {
        "run": run,
        "arm": planned.arm,
        "qid": q.qid,
        "set": q.set,
        "split": q.split,
        "entity": q.entity,
        "as_of": q.as_of.isoformat(),
        "gold": list(q.gold),
        "source": q.source,
        "expect": q.expect,
        "outcome": result.outcome,
        "reason": result.reason.value if isinstance(result, Refusal) else None,
        "caveats": [c.value for c in getattr(result, "caveats", [])],
        "degraded": list(response.degraded),
        "path": state.path if state is not None else None,
        "claims": claims,
        "provisions": [p.para_id for p in result.provisions]
        if isinstance(result, RetrievalOnly)
        else [],
        "ranked": _ranked(state, ARMS[planned.arm]) if state is not None else [],
        "dropped": [[d.para_id, d.reason] for d in response.dropped],
        "checks_failed": sorted(k for k, r in checks.items() if not r.passed),
        "retried": "retry" in checks,
        "retry_recovered": bool(checks.get("retry") and checks["retry"].passed),
        "cost_eur": response.cost_eur,  # what this question costs in production
        "latency_ms": response.latency_ms,
        "model_calls": sum(n.calls for n in state.node_log) if state is not None else 0,
        "fresh_calls": fresh,  # what this run actually paid for
        "spend_eur": round(spent, 6),
    }


# --------------------------------------------------------------------------- the run


@dataclass
class Harness:
    schema: str
    run: str
    models: dict[str, CassetteModel]
    embedder: CachedEmbedder
    open_index: Callable[[], Any]
    entities: Mapping[str, str]
    rows_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.rows_path = RUNS / self.run / "rows.jsonl"

    def done(self) -> set[tuple[str, str]]:
        if not self.rows_path.exists():
            return set()
        return {
            (r["arm"], r["qid"])
            for r in (json.loads(x) for x in self.rows_path.read_text().splitlines() if x)
        }

    def spent(self) -> float:
        return sum(m.spent_eur for m in self.models.values()) + self.embedder.spent_eur

    def fresh(self) -> int:
        return sum(m.fresh for m in self.models.values()) + self.embedder.fresh

    def ask(self, planned: Planned) -> dict[str, Any]:
        from extant.agent.service import Service
        from extant.guards.middleware import MemoryUsageStore

        service = Service(
            index_version=self.schema,
            open_index=self.open_index,
            models=dict(self.models),
            store=MemoryUsageStore(cap=1_000_000),  # eval questions never move the real cap
            entities=self.entities,
            log=lambda record: None,
            day=lambda: EVAL_TODAY,
            arm=ARMS[planned.arm],
        )
        before, fresh_before = self.spent(), self.fresh()
        q = planned.q
        served = service.run(
            q.text, entity=q.entity, as_of=q.as_of, request_id=f"{self.run}:{planned.arm}:{q.qid}"
        )
        # An outage is not the system's answer to the question. Postgres down (the graph never
        # ran) or a model down after its retry (retrieval only) stops the run unrecorded, so a
        # resumed run asks the question again instead of scoring the outage.
        if served.state is None or "retrieval_only" in served.response.degraded:
            raise InfrastructureError(
                f"{planned.arm} {q.qid}: {served.response.degraded}; stopped, nothing recorded"
            )
        row = row_for(planned, served, self.run, self.spent() - before, self.fresh() - fresh_before)
        self.rows_path.parent.mkdir(parents=True, exist_ok=True)
        with self.rows_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row


def _real_harness(run: str, schema: str) -> tuple[Harness, Any]:
    import psycopg
    from psycopg import sql

    from extant.agent.index import PgIndex
    from extant.agent.settings import load_agent_settings
    from extant.guards.middleware import MemoryUsageStore
    from extant.index.settings import load_index_settings

    agent, index_settings = load_agent_settings(), load_index_settings()
    store = CassetteStore(CASSETTES / "models.jsonl")
    guarded = agent.models(MemoryUsageStore(cap=1_000_000))
    models = {
        role: CassetteModel(m.model, store, m, agent.eur_per_usd) for role, m in guarded.items()
    }
    embedder = CachedEmbedder(
        index_settings.embedder(), CassetteStore(CASSETTES / "embeddings.jsonl")
    )
    conn = psycopg.connect(index_settings.writer_dsn.get_secret_value(), autocommit=True)
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    entities = dict(PgIndex(conn, schema, embedder).entity_labels())
    harness = Harness(
        schema,
        run,
        models,
        embedder,
        lambda: PgIndex(conn, schema, embedder),
        entities,
    )
    return harness, conn


def _summary_path(run: str) -> Path:
    return RUNS / run / "summary.json"


def ask_all(
    run: str,
    schema: str,
    arms: Sequence[str],
    splits: Sequence[str],
    budget: float = BUDGET_EUR,
    only: set[str] | None = None,
) -> dict[str, Any]:
    from evals.retrieval import count_test_run

    planned = [p for p in plan(arms, splits) if only is None or p.q.qid in only]
    cost = CostModel.from_fixtures()
    est = estimate(planned, cost)
    if any(p.q.split == "test" for p in planned):
        count_test_run([f"answers:{s}" for s in ("answerable", "as_of", "unanswerable")])
    harness, conn = _real_harness(run, schema)
    done = harness.done()
    path = _summary_path(run)
    prior = json.loads(path.read_text()) if path.exists() else {}
    paid_before = float(prior.get("spend_eur", 0.0))
    started = datetime.now().isoformat(timespec="seconds")
    t0 = time.perf_counter()
    stopped: str | None = None
    try:
        for i, p in enumerate(planned):
            if (p.arm, p.q.qid) in done:
                continue
            if paid_before + harness.spent() + expected_eur(p, cost) > budget:
                left = sum(1 for x in planned[i:] if (x.arm, x.q.qid) not in done)
                stopped = f"budget EUR {budget:.2f} reached with {left} planned questions left"
                print(f"stopped: {stopped}", flush=True)
                break
            row = harness.ask(p)
            print(
                f"[{i + 1}/{len(planned)}] {p.arm:16} {p.q.qid:8} {row['outcome']:14}"
                f" {row['reason'] or ''} EUR {row['spend_eur']:.4f}",
                flush=True,
            )
    finally:
        conn.close()
        summary = {
            "run": run,
            "schema": schema,
            "arms": list(arms),
            "scope": {a: SCOPE_TEXT[a] for a in arms},
            "splits": list(splits),
            "planned": len(planned),
            "budget_eur": budget,
            "stopped": stopped,
            "started": prior.get("started", started),
            "finished": datetime.now().isoformat(timespec="seconds"),
            "estimate_eur": round(est.total, 4),
            "spend_eur": round(paid_before + harness.spent(), 6),
            "fresh_calls": prior.get("fresh_calls", 0) + harness.fresh(),
            "seconds": round(prior.get("seconds", 0.0) + time.perf_counter() - t0, 1),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.answers.run")
    parser.add_argument("command", choices=["plan", "ask"])
    parser.add_argument("--run", default=EVAL_TODAY.isoformat())
    parser.add_argument("--schema", help="default: EXTANT_BUILD_SCHEMA")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--splits", default="dev,test")
    parser.add_argument("--only", help="comma separated question ids, for a smoke run")
    parser.add_argument("--budget", type=float, default=BUDGET_EUR)
    args = parser.parse_args(argv)
    arms, splits = args.arms.split(","), args.splits.split(",")
    planned = plan(arms, splits)
    if args.only:
        keep = set(args.only.split(","))
        planned = [p for p in planned if p.q.qid in keep]
    est = estimate(planned, CostModel.from_fixtures())
    print(show(est, splits))
    if args.command == "plan":
        return 0  # the dry run: no model, no embedding, no database
    only = set(args.only.split(",")) if args.only else None
    schema = args.schema or _build_schema()
    summary = ask_all(args.run, schema, arms, splits, args.budget, only)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
