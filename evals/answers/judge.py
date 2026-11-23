"""The judge over every answered claim, and the hand labels its agreement rate comes from.

    uv run python -m evals.answers.judge pass --run 2026-11-23    # judge every claim (cassetted)
    uv run python -m evals.answers.judge draw --run 2026-11-23    # 50 pairs for a person, blind
    uv run python -m evals.answers.judge agreement --run 2026-11-23

Order matters: the agreement rate is measured before any groundedness number is read, and
every groundedness figure is shown beside it.

``draw`` samples 50 claim and paragraph pairs from the baseline arm, stratified so that half
sit near the overlap threshold (0.35 to 0.65), where the judge is actually invoked, since
agreement on easy pairs says little. The label file shows a person exactly what the judge
was shown and nothing else: no judge verdict, no overlap. The judge's verdicts go to a
separate key file that the file's preparation never reads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

from evals.answers.metrics import Agreement, agreement
from evals.answers.run import CASSETTES, EVAL_TODAY, RUNS, CassetteModel, CassetteStore
from evals.answers.sets import _build_schema
from extant.agent import prompts
from extant.agent.checks import overlap
from extant.agent.models import JudgeOutput
from extant.agent.runtime import parsed

LABELS = Path(__file__).resolve().parents[1] / "judge-labels"
NEAR = (0.35, 0.65)
SAMPLE = 50
SEED = 20261123
_VERDICT = re.compile(r"^\*\*Human verdict:\*\*[ \t]*(?P<v>.*)$", re.M)
_ID = re.compile(r"^## \d+\. (?P<id>[0-9a-f]{12})$", re.M)


def _rows(run: str) -> list[dict[str, Any]]:
    path = RUNS / run / "rows.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def judge_pass(run: str, schema: str) -> Path:
    """Ask the judge about every answered claim of every arm. Identical prompts are one
    cassette entry, so the same claim over the same paragraphs is judged once."""
    import psycopg
    from psycopg import sql

    from extant.agent.settings import load_agent_settings
    from extant.guards.middleware import MemoryUsageStore
    from extant.index.settings import load_index_settings

    agent = load_agent_settings()
    real = agent.models(MemoryUsageStore(cap=1_000_000))["judge"]
    judge = CassetteModel(real.model, CassetteStore(CASSETTES / "models.jsonl"), real)
    out = RUNS / run / "judged.jsonl"
    rows = _rows(run)
    cited = {c["para_id"] for r in rows for cl in r["claims"] for c in cl["citations"]}
    dsn = load_index_settings().writer_dsn.get_secret_value()
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        bodies: dict[str, str] = dict(
            conn.execute(
                "SELECT para_id, body FROM paragraphs WHERE para_id = ANY(%s)", (list(cited),)
            ).fetchall()
        )
    lines = []
    for r in rows:
        for i, cl in enumerate(r["claims"]):
            paras = [bodies[c["para_id"]] for c in cl["citations"]]
            user = prompts.judge_user(cl["text"], paras)
            done = judge.complete(JudgeOutput, system=prompts.JUDGE_SYSTEM, user=user)
            lines.append(
                {
                    "arm": r["arm"],
                    "qid": r["qid"],
                    "set": r["set"],
                    "split": r["split"],
                    "claim": i,
                    "pair": hashlib.sha256(user.encode()).hexdigest()[:12],
                    "overlap": round(overlap(cl["text"], cl["quotes"]), 3),
                    "supported": parsed(done, JudgeOutput).supported,
                    "user": user,
                }
            )
    out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), "utf-8")
    summary_path = RUNS / run / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["judge_pass_eur"] = round(summary.get("judge_pass_eur", 0.0) + judge.spent_eur, 6)
    summary["judge_pass_calls"] = summary.get("judge_pass_calls", 0) + judge.fresh
    summary_path.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(f"judged {len(lines)} claims; {judge.fresh} new calls, EUR {judge.spent_eur:.4f}")
    return out


def _judged(run: str) -> list[dict[str, Any]]:
    path = RUNS / run / "judged.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def draw(run: str, out: Path | None = None) -> Path:
    """The blind label file and its separate key."""
    seen: dict[str, dict[str, Any]] = {}
    for j in _judged(run):
        if j["arm"] == "baseline":
            seen.setdefault(j["pair"], j)
    pairs = sorted(seen.values(), key=lambda j: j["pair"])
    near = [j for j in pairs if NEAR[0] <= j["overlap"] <= NEAR[1]]
    far = [j for j in pairs if not NEAR[0] <= j["overlap"] <= NEAR[1]]
    rng = random.Random(SEED)
    take_near = rng.sample(near, min(len(near), SAMPLE // 2))
    take_far = rng.sample(far, min(len(far), SAMPLE - len(take_near)))
    chosen = take_near + take_far
    rng.shuffle(chosen)
    out = out or LABELS / f"{EVAL_TODAY.isoformat()}.md"
    lines = [
        f"# Judge hand labels, {EVAL_TODAY.isoformat()}",
        "",
        f"{len(chosen)} claim and paragraph pairs from the baseline arm of run `{run}`,"
        f" {len(take_near)} with content word overlap near the threshold and {len(take_far)}"
        " elsewhere, in random order. Each is exactly what the judge was shown. On each pair's"
        " verdict line write `supported` or `unsupported`, judging only whether the paragraphs"
        " support the claim, without outside knowledge.",
        "",
    ]
    for n, j in enumerate(chosen, 1):
        lines += [
            f"## {n}. {j['pair']}",
            "",
            "```",
            j["user"],
            "```",
            "",
            "**Human verdict:** ",
            "",
        ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    key = {j["pair"]: {"supported": j["supported"], "overlap": j["overlap"]} for j in chosen}
    out.with_suffix(".key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    return out


def read_labels(path: Path) -> list[tuple[str, bool | None]]:
    text = path.read_text(encoding="utf-8")
    ids = [m.group("id") for m in _ID.finditer(text)]
    verdicts = [m.group("v").strip().lower() for m in _VERDICT.finditer(text)]
    if len(ids) != len(verdicts):
        raise ValueError(f"{path}: {len(ids)} pairs but {len(verdicts)} verdict lines")
    out: list[tuple[str, bool | None]] = []
    for pid, v in zip(ids, verdicts, strict=True):
        if v not in ("", "supported", "unsupported"):
            raise ValueError(f"{path}: pair {pid}: write supported or unsupported, not {v!r}")
        out.append((pid, None if not v else v == "supported"))
    return out


def measure(path: Path) -> Agreement | None:
    """Agreement once every pair is labelled; None while any is blank."""
    labels = read_labels(path)
    if any(v is None for _, v in labels):
        return None
    key = json.loads(path.with_suffix(".key.json").read_text(encoding="utf-8"))
    return agreement([(bool(key[pid]["supported"]), bool(v)) for pid, v in labels])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.answers.judge")
    parser.add_argument("command", choices=["pass", "draw", "agreement"])
    parser.add_argument("--run", default=EVAL_TODAY.isoformat())
    parser.add_argument("--schema", help="default: EXTANT_BUILD_SCHEMA")
    args = parser.parse_args(argv)
    args.schema = args.schema or _build_schema()
    if args.command == "pass":
        judge_pass(args.run, args.schema)
    elif args.command == "draw":
        print(f"wrote {draw(args.run)}")
    else:
        a = measure(LABELS / f"{EVAL_TODAY.isoformat()}.md")
        if a is None:
            print("not every pair is labelled yet")
            return 1
        print(
            f"agreement {a.agree}/{a.n} = {a.rate.value:.2f} [{a.rate.low:.2f}, {a.rate.high:.2f}],"
            f" kappa {a.kappa:.2f}"
            if a.kappa is not None
            else ""
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
