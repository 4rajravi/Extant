"""The retrieval eval runner.

    uv run python -m evals.retrieval --schema idx_rbi_... --out results.parquet
    uv run python -m evals.retrieval --schema idx_rbi_... --arms all \
        --sample-schema idx_rbisample_... --out evals/results/run.parquet
    uv run python -m evals.retrieval --schema idx_rbi_... --split test --out ...   # counted

It reads an index build by schema name through the reader role, embeds nothing (every query
vector comes from the committed cache, and a miss stops the run), and writes Parquet:

- ``<out>``: one row per query per arm, with the gold ids, the returned ids in rank order and
  every per query metric, so a surprising number is answered by filtering, not rerunning.
- ``<out stem>.summary.parquet``: one row per set, arm and bucket (all, main, holdout), made
  by ``evals.metrics.summarise``, with n, intervals, the audited label precision and the
  test-split evaluation count. The renderer prints this file and computes nothing.

The runner defaults to the dev split. ``--split test`` needs saying, and every test run is
counted in ``evals/test-evaluations.json``, which the table carries: a test set looked at
forty times is a dev set.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg import sql
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from evals.arms import ARMS, BY_NAME, GATED, TOP_N, Arm
from evals.audit import read_audit
from evals.metrics import (
    DEPTHS,
    SetRows,
    as_of_violations,
    entity_violations,
    estimate_of,
    first_hit_rank,
    summarise,
)
from evals.query_vectors import CachedQueryEmbedder
from evals.sets import EvalQuery, EvalSet, Split, load_sets, read_sample
from extant.index.embed import Embedder
from extant.retrieval import Candidate, RetrievalResult, retrieve

REPO = Path(__file__).resolve().parents[1]
COUNTER = REPO / "evals" / "test-evaluations.json"


class EvalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXTANT_", env_file=".env", extra="ignore")
    reader_dsn: SecretStr


class Reranker(Protocol):
    def rerank(self, query: str, candidates: Sequence[Candidate]) -> list[Candidate]: ...


# --------------------------------------------------------------------------- the test counter


def read_counter(path: Path = COUNTER) -> dict[str, int]:
    if not path.exists():
        return {}
    counts: dict[str, int] = json.loads(path.read_text(encoding="utf-8"))
    return counts


def count_test_run(sets: Iterable[str], path: Path = COUNTER) -> dict[str, int]:
    """One more evaluation of each set's test half, persisted before any result exists, so a
    run that crashes halfway still counts as a look."""
    counts = read_counter(path)
    for name in sets:
        counts[name] = counts.get(name, 0) + 1
    path.write_text(json.dumps(counts, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return counts


# --------------------------------------------------------------------------- one query


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def score_query(
    q: EvalQuery, result: RetrievalResult, ranked: Sequence[Candidate]
) -> dict[str, Any]:
    """The per query row: what came back and every per query metric."""
    returned = [c.para_id for c in ranked]
    rank_of = {c.para_id: c.rank for c in ranked}
    context = [c for c in result.context if c.expanded_from in rank_of]
    # Documents the result names: looked up records first (withdrawals, concordance), then the
    # documents of the ranked paragraphs.
    looked_up = [
        *(w["withdrawn_by_doc_id"] for m in result.references for w in m.withdrawals),
        *(d for m in result.references for d in m.consolidated_into),
    ]
    returned_docs = _dedupe([*looked_up, *(c.doc_id for c in ranked)])
    para_rank = first_hit_rank(returned, q.gold_para_ids)
    gold = set(q.gold_para_ids)
    via_context = [rank_of[c.expanded_from or ""] for c in context if c.para_id in gold]
    with_context = min([r for r in (para_rank, *via_context) if r is not None], default=None)
    everything = [*ranked, *context]
    return {
        "query_id": q.query_id,
        "group": q.group,
        "form": q.form,
        "holdout": q.holdout,
        "level": q.level,
        "text": q.text,
        "entity_codes": list(q.entity_codes),
        "as_of": q.as_of,
        "gold_para_ids": list(q.gold_para_ids),
        "gold_doc_ids": list(q.gold_doc_ids),
        "path": result.path,
        "returned_para_ids": returned,
        "returned_doc_ids": returned_docs,
        "context_para_ids": [c.para_id for c in context],
        "para_rank": para_rank,
        "doc_rank": first_hit_rank(returned_docs, q.gold_doc_ids),
        "para_rank_with_context": with_context,
        "returned_count": len(everything),
        "entity_violations": entity_violations(
            [c.entity_codes for c in everything], q.entity_codes
        ),
        "as_of_violations": as_of_violations(
            [(c.valid_from, c.valid_to) for c in everything], q.as_of
        ),
    }


def evaluate(
    conn: psycopg.Connection,
    queries: Sequence[EvalQuery],
    arm: Arm,
    embedder: Embedder,
    *,
    reranker: Reranker | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for q in queries:
        result = retrieve(
            conn, q.text, q.entity_codes, q.as_of, embedder=embedder, options=arm.options
        )
        ranked = result.candidates
        if arm.rerank:
            if reranker is None:
                raise RuntimeError(f"arm {arm.name} needs a reranker")
            ranked = reranker.rerank(q.text, ranked)[:TOP_N]
        rows.append(score_query(q, result, ranked))
    return rows


# --------------------------------------------------------------------------- a run


def _search_path(conn: psycopg.Connection, schema: str) -> None:
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))


def run(
    *,
    connect: Callable[[], psycopg.Connection],
    schema: str,
    sets: dict[str, EvalSet],
    split: Split,
    arms: Sequence[Arm],
    sample_schema: str | None = None,
    embedders: Callable[[Arm], Embedder] | None = None,
    reranker: Reranker | None = None,
    counter: Path = COUNTER,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every arm over every set's ``split``: the per query rows and the per set summaries."""
    test_counts = count_test_run(sets, counter) if split == "test" else read_counter(counter)
    make_embedder = embedders or (lambda arm: CachedQueryEmbedder(arm.query_model))
    cache: dict[Any, Embedder] = {}
    rows: list[dict[str, Any]] = []
    sample = read_sample() if any(a.corpus == "sample" for a in arms) else None
    with connect() as conn:
        for arm in arms:
            if arm.corpus == "sample":
                if sample_schema is None or sample is None:
                    raise SystemExit(
                        f"error: arm {arm.name} runs on the sample: pass --sample-schema"
                    )
                target = sample_schema
                corpus = f"sample, {sample['documents']} of {sample['corpus_documents']} documents"
                wanted = set(sample["queries"])
                arm_sets = {
                    n: [q for q in s.split(split) if q.query_id in wanted] for n, s in sets.items()
                }
            else:
                target, corpus = schema, "full"
                arm_sets = {n: s.split(split) for n, s in sets.items()}
            _search_path(conn, target)
            embedder = cache.setdefault(arm.query_model, make_embedder(arm))
            for name, queries in sorted(arm_sets.items()):
                if not queries:
                    continue
                for row in evaluate(conn, queries, arm, embedder, reranker=reranker):
                    rows.append(
                        {
                            "set": name,
                            "split": split,
                            "arm": arm.name,
                            "ablation": arm.ablation,
                            "corpus": corpus,
                            "schema": target,
                            **row,
                        }
                    )
    return rows, summaries(rows, arms, test_counts)


def summaries(
    rows: Sequence[dict[str, Any]], arms: Sequence[Arm], test_counts: dict[str, int]
) -> list[dict[str, Any]]:
    order = {a.name: i for i, a in enumerate(ARMS)}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["set"], r["arm"]), []).append(r)
    out = []
    for (name, arm_name), group in sorted(
        groups.items(), key=lambda kv: (kv[0][0], order[kv[0][1]])
    ):
        set_rows = SetRows(group)
        audit = read_audit(name)
        for holdout in (None, False, True):
            s = summarise(set_rows, holdout=holdout)
            s.update(
                {
                    "ablation": BY_NAME[arm_name].ablation,
                    "description": BY_NAME[arm_name].description,
                    "reference": BY_NAME[arm_name].reference,
                    "schema": group[0]["schema"],
                    "label_precision": audit.precision,
                    "audit_judged": audit.judged,
                    "audit_pairs": audit.pairs,
                    "audit_by": audit.auditor,
                    "test_evaluations": test_counts.get(name, 0),
                }
            )
            out.append(s)
    _mark_differences(out)
    return out


def _mark_differences(summary: list[dict[str, Any]]) -> None:
    """Against the reference arm of the same ablation, on the same set, corpus and bucket:
    the recall depths whose intervals do not overlap. Everything else is no measurable
    difference, whatever the point estimates say."""
    index = {(s["set"], s["arm"], s["bucket"]): s for s in summary}
    for s in summary:
        ref_name = BY_NAME[s["arm"]].compare_with
        s["compared_with"] = ref_name
        s["differs_at"] = None
        ref = index.get((s["set"], ref_name, s["bucket"])) if ref_name else None
        if ref is None:
            continue
        diffs = []
        for level in ("para", "doc", "para_ctx"):
            for k in DEPTHS:
                name = f"recall@{k}_{level}"
                mine, theirs = estimate_of(s, name), estimate_of(ref, name)
                if mine.n and theirs.n and not mine.overlaps(theirs):
                    diffs.append(f"{level}@{k}")
        s["differs_at"] = ", ".join(diffs) if diffs else "none"


# --------------------------------------------------------------------------- files


def write_parquet(rows: Sequence[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(list(rows)), path, compression="zstd")


def summary_path(results: Path) -> Path:
    return results.with_name(results.stem + ".summary.parquet")


# --------------------------------------------------------------------------- command line


def _arms(spec: str) -> list[Arm]:
    if spec == "all":
        return list(ARMS)
    if spec == "full":
        return [a for a in ARMS if a.corpus == "full"]
    if spec == "sample":
        return [a for a in ARMS if a.corpus == "sample"]
    names = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in BY_NAME]
    if unknown:
        raise SystemExit(f"error: unknown arms {unknown}; known: {sorted(BY_NAME)}")
    return [BY_NAME[n] for n in names]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.retrieval")
    p.add_argument("--schema", required=True, help="the full index build to evaluate")
    p.add_argument("--out", required=True, type=Path)
    # Dev unless told otherwise: touching the test half has to be said out loud.
    p.add_argument("--split", choices=["dev", "test"], default="dev")
    p.add_argument("--arms", default=GATED, help="names, or full, sample or all")
    p.add_argument("--sets", help="comma separated set names (default: every built set)")
    p.add_argument("--sample-schema", help="the reduced-corpus build for arms 4 to 6")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    arms = _arms(args.arms)
    sets = load_sets(args.sets.split(",") if args.sets else None)
    dsn = EvalSettings().reader_dsn.get_secret_value()
    reranker: Reranker | None = None
    if any(a.rerank for a in arms):
        from evals.ablation.models import CrossEncoderReranker

        reranker = CrossEncoderReranker()
    rows, summary = run(
        connect=lambda: psycopg.connect(dsn, autocommit=True),
        schema=args.schema,
        sets=sets,
        split=args.split,
        arms=arms,
        sample_schema=args.sample_schema,
        reranker=reranker,
    )
    write_parquet(rows, args.out)
    write_parquet(summary, summary_path(args.out))
    for s in summary:
        if s["bucket"] != "all":
            continue
        r10 = s["recall@10_para"] if s["n_paragraph_gold"] else s["recall@10_doc"]
        level = "para" if s["n_paragraph_gold"] else "doc"
        shown = "n/a" if r10 is None else f"{r10:.3f}"
        print(
            f"{s['set']:<12} {s['arm']:<20} n={s['n']:<5} recall@10 {level} {shown}"
            f"  entity violations {s['entity_violations']}, as-of {s['as_of_violations']}"
        )
    print(f"wrote {args.out} and {summary_path(args.out).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
