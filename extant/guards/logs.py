"""Structured request logging: one JSON record per request, to stdout, which Cloud Run
collects.

The record carries what retrieval returned and what the gate dropped, with scores and the
index schema name, so a disputed answer can be replayed against the exact build it came
from; logging only the citations would not allow that. It carries the redacted question and
its hash, never the question as typed: the hash is computed over the redacted form, so
repeat questions can be counted without storing what was pasted.

``summarise`` turns a run of records into the counters worth watching.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from extant.agent.models import AgentState, Answer, AskResponse, Refusal, RetrievalOnly


def query_sha256(redacted: str) -> str:
    return hashlib.sha256(redacted.encode("utf-8")).hexdigest()


def _citations(response: AskResponse) -> list[str]:
    result = response.result
    if isinstance(result, Answer):
        ids = [cit.para_id for claim in result.claims for cit in claim.citations]
    elif isinstance(result, RetrievalOnly):
        ids = [p.para_id for p in result.provisions]
    else:
        ids = []
    return list(dict.fromkeys(ids))


def request_record(
    *,
    at: datetime,
    request_id: str,
    query_redacted: str,
    response: AskResponse,
    state: AgentState | None,
    model_calls: int,
) -> dict[str, Any]:
    result = response.result
    return {
        "ts": at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "request_id": request_id,
        "index_version": response.index_version,
        "query_redacted": query_redacted,
        "query_sha256": query_sha256(query_redacted),
        "entity_type": response.entity_type,
        "as_of": response.as_of.isoformat(),
        "path": state.path if state else None,
        "retrieved": [[r.para_id, r.fusion_score] for r in response.retrieved],
        "dropped": [[d.para_id, d.reason] for d in response.dropped],
        "citations": _citations(response),
        "outcome": result.outcome,
        "refusal_reason": result.reason.value if isinstance(result, Refusal) else None,
        "caveats": [c.value for c in getattr(result, "caveats", [])],
        "node_costs": {
            n.node: {"ms": round(n.latency_ms), "eur": round(n.eur, 6)}
            for n in (state.node_log if state else [])
        },
        "total_eur": response.cost_eur,
        "latency_ms": response.latency_ms,
        "degraded": list(response.degraded),
        "redactions": list(response.redactions),
        "model_calls": model_calls,
    }


def emit(record: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def emit_stderr(record: Mapping[str, Any]) -> None:
    """For a process whose stdout is a protocol channel (the MCP server over stdio)."""
    sys.stderr.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stderr.flush()


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[round(q * 100) - 1]


def summarise(records: Iterable[Mapping[str, Any]], *, cap: int) -> dict[str, Any]:
    """The counters worth watching, over a run of request records.

    Alias hits on para_id resolution are not here: nothing in serving resolves a retired
    para_id yet, so there is nothing to count until the citation lookup that does exists."""
    rows = list(records)
    n = len(rows)
    costs = [float(r["total_eur"]) for r in rows]
    answered_by_day = Counter(r["ts"][:10] for r in rows if r["outcome"] == "answer")
    return {
        "requests": n,
        "by_outcome": dict(Counter(r["outcome"] for r in rows)),
        "by_refusal_reason": dict(
            Counter(r["refusal_reason"] for r in rows if r["refusal_reason"])
        ),
        "fast_path_share": (sum(r["path"] == "fast" for r in rows) / n) if n else None,
        "dropped_per_request": (sum(len(r["dropped"]) for r in rows) / n) if n else None,
        "answered_by_day_against_cap": {
            d: f"{c} of {cap}" for d, c in sorted(answered_by_day.items())
        },
        "model_calls": sum(int(r["model_calls"]) for r in rows),
        "cost_eur_p50": _pct(costs, 0.50),
        "cost_eur_p95": _pct(costs, 0.95),
        "redactions": dict(Counter(k for r in rows for k in r["redactions"])),
        "degraded": dict(Counter(k for r in rows for k in r["degraded"])),
    }
