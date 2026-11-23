"""Record the graph's fixtures: real retrieval and real model responses, replayed in tests.

    uv run python -m extant.agent.record --schema idx_rbi_20261120_38ede3c

Each spec runs once through the real graph, on the real build, with the configured models.
Every index call and every model call is kept, and written with the final state to
``tests/fixtures/agent/<name>.json``. ``tests/integration/test_agent_replay.py`` replays them
with no database, no network and no cost.

Some outcomes cannot arise from the RBI corpus as built: it holds no FAQ or press release,
so the non-binding classes never fire, and retrieval degrades only when the embedding call
fails. Those fixtures are derived from a recorded one by a single named change, chosen so the
replay asks no model anything it was not asked when recording: the change touches only what
the composer's prompt does not show (authority rank, validity, the degraded flag), or the
recorded composer response itself. Each derived file says what it was derived from and how.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

from extant.agent.graph import ask, build
from extant.agent.index import PgIndex, RecordingIndex
from extant.agent.llm import RecordingModel
from extant.agent.runtime import Deps
from extant.agent.settings import load_agent_settings

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "agent"
TODAY = date(2026, 11, 22)


@dataclass(frozen=True)
class Spec:
    name: str
    question: str
    entity: str | None
    as_of: date | None
    expect: str  # what the fixture is meant to exercise


SPECS = (
    Spec(
        "full_answer_as_of_assumed",
        "What minimum capital to risk weighted assets ratio must a small finance bank maintain?",
        "sfb",
        None,
        "an answer on the full path; as_of_assumed",
    ),
    Spec(
        "full_answer_third_person",
        "Can a commercial bank declare a dividend if its net NPA ratio is above 6 per cent?",
        "cb",
        date(2026, 11, 20),
        "an answer with no caveat: 'can a commercial bank' is not the asker's own permission",
    ),
    Spec(
        "full_answer_not_legal_advice",
        "Can we declare a dividend if we are a commercial bank whose net NPA ratio is above"
        " 6 per cent?",
        "cb",
        None,
        "an answer with the not_legal_advice and as_of_assumed caveats",
    ),
    Spec(
        "fast_answer_consolidated",
        "What replaced circular DOR.ACC.REC.87/21-02-067/2025-26?",
        "cb",
        date(2026, 11, 20),
        "the fast path: zero model and zero embedding calls",
    ),
    Spec(
        "fast_answer_with_caveats",
        "Can we still rely on circular DOR.ACC.REC.87/21-02-067/2025-26?",
        "cb",
        None,
        "a fast answer carrying not_legal_advice and as_of_assumed, at zero cost",
    ),
    Spec(
        "fast_superseded_only",
        "Is circular DBOD.No.Dir.BC.1/13.03.00/2012-13 still in force?",
        "cb",
        date(2026, 11, 20),
        "superseded_only: withdrawn before the date asked",
    ),
    Spec(
        "fast_withdrawn_after_the_date",
        "Is circular DBOD.No.Dir.BC.1/13.03.00/2012-13 still in force?",
        "cb",
        date(2024, 3, 14),
        "no_support_found that says it may still have applied",
    ),
    Spec(
        "out_of_scope_by_model",
        "What documents does a stockbroker need from me to open a demat account?",
        None,
        date(2026, 11, 20),
        "out_of_scope decided by the cheap model",
    ),
    Spec(
        "computation_by_pattern",
        "Calculate the CRAR for my bank if Tier 1 capital is 500 crore and RWA is 4,000 crore.",
        "cb",
        date(2026, 11, 20),
        "computation_requested, no model call",
    ),
    Spec(
        "entity_required_no_entity",
        "What is the maximum exposure a lender may take on a single borrower?",
        None,
        date(2026, 11, 20),
        "entity_required when scopes disagree, or an answer when they agree",
    ),
    Spec(
        "diff_two_dates",
        "How did the minimum capital to risk weighted assets ratio for a small finance bank"
        " change between 15 January 2026 and 20 November 2026?",
        "sfb",
        None,
        "the diff path: one model call, both versions quoted",
    ),
    Spec(
        "full_no_support",
        "What does RBI require of banks when lending against lunar mining rights?",
        "cb",
        date(2026, 11, 20),
        "insufficient, so no_support_found",
    ),
)


def _record(spec: Spec, conn: psycopg.Connection, schema: str) -> dict[str, Any]:
    from extant.index.settings import load_index_settings

    settings = load_agent_settings()
    models_store: dict[str, Any] = {}
    real = settings.models()
    models = {k: RecordingModel(v, models_store) for k, v in real.items()}
    base = PgIndex(conn, schema, load_index_settings().embedder())
    index = RecordingIndex(base)
    deps = Deps(
        models=models, index=index, eur_per_usd=settings.eur_per_usd, entities=base.entity_labels()
    )
    state = ask(
        build(deps),
        spec.question,
        index_version=schema,
        entity=spec.entity,
        as_of=spec.as_of,
        today=TODAY,
        request_id=spec.name,
    )
    return {
        "spec": {
            "name": spec.name,
            "question": spec.question,
            "entity": spec.entity,
            "as_of": spec.as_of.isoformat() if spec.as_of else None,
            "expect": spec.expect,
        },
        "today": TODAY.isoformat(),
        "index_version": schema,
        "embedding_model": index.embedding_model,
        "entities": deps.entities,
        "models": models_store,
        "index": index.store,
        "recorded": _summary(state),
    }


def _summary(state: Any) -> dict[str, Any]:
    return {
        "path": state.path,
        "outcome": state.outcome,
        "refusal_reason": state.refusal_reason.value if state.refusal_reason else None,
        "caveats": [c.value for c in state.caveats],
        "node_log": [n.model_dump(mode="json") for n in state.node_log],
        "response": state.response.model_dump(mode="json") if state.response else None,
    }


# --------------------------------------------------------------------------- derived fixtures


def _first_cited(data: dict[str, Any]) -> str:
    claims = data["recorded"]["response"]["result"]["claims"]
    return str(claims[0]["citations"][0]["para_id"])


def _patch_candidates(data: dict[str, Any], fn: Callable[[dict[str, Any]], None]) -> None:
    for key, value in data["index"].items():
        if key.startswith('["search"') and isinstance(value, dict):
            for c in value["candidates"] + value["neighbours"]:
                fn(c)


def _non_binding_only(d: dict[str, Any]) -> str:
    def make_faq(c: dict[str, Any]) -> None:
        c["authority_rank"], c["binding"] = 60, False

    _patch_candidates(d, make_faq)
    return "every retrieved paragraph set to authority rank 60 (FAQ, non-binding)"


def _non_binding_cited(d: dict[str, Any]) -> str:
    claims = d["recorded"]["response"]["result"]["claims"]
    first = {c["para_id"] for c in claims[0]["citations"]}
    others = {c["para_id"] for claim in claims[1:] for c in claim["citations"]} - first
    if not others:
        raise ValueError("the base answer cites only the first claim's paragraphs")

    def faq_if_other(c: dict[str, Any]) -> None:
        if c["para_id"] in others:
            c["authority_rank"], c["binding"] = 60, False

    _patch_candidates(d, faq_if_other)
    return (
        f"paragraphs cited by later claims ({', '.join(sorted(others))}) set to authority rank"
        f" 60 (FAQ); the first claim's {', '.join(sorted(first))} stays binding"
    )


def _amendment_pending(d: dict[str, Any]) -> str:
    target = _first_cited(d)

    def pending(c: dict[str, Any]) -> None:
        if c["para_id"] == target:
            c["valid_to"] = "2027-04-01"

    _patch_candidates(d, pending)
    return f"{target} given a later version taking effect on 2027-04-01"


def _degraded(d: dict[str, Any]) -> str:
    for key, value in d["index"].items():
        if key.startswith('["search"') and isinstance(value, dict):
            value["degraded"], value["embedding_calls"], value["embedding_tokens"] = (
                ["lexical_only"],
                0,
                0,
            )
    return "the recorded search marked lexical only, as when the embedding call fails"


def _broken_quote(d: dict[str, Any]) -> str:
    for entry in d["models"].values():
        if entry["schema"] == "ComposerOutput" and entry["parsed"]["claims"]:
            q = entry["parsed"]["claims"][0]["quotes"][0]
            entry["parsed"]["claims"][0]["quotes"][0] = q + " (as amended)"
            return "the composer's first quote given words its paragraph does not contain"
    raise ValueError("no composer response to break")


# The base must be a recorded answer. full_answer_as_of_assumed was meant to be one and
# refused when recorded (a claim restated "15 per cent" without quoting it), so the base is
# the third person dividend question, which answered with four claims on four paragraphs.
BASE = "full_answer_third_person"
DERIVED: dict[str, tuple[str, Callable[[dict[str, Any]], str]]] = {
    "derived_non_binding_support_only": (BASE, _non_binding_only),
    "derived_non_binding_cited": (BASE, _non_binding_cited),
    "derived_amendment_pending": (BASE, _amendment_pending),
    "derived_degraded_retrieval": (BASE, _degraded),
    "derived_verification_failed": (BASE, _broken_quote),
}


def derive() -> list[Path]:
    out = []
    for name, (base, change) in DERIVED.items():
        data = copy.deepcopy(json.loads((FIXTURES / f"{base}.json").read_text(encoding="utf-8")))
        how = change(data)
        data["spec"] = {**data["spec"], "name": name}
        data["derived_from"] = {"fixture": base, "change": how}
        data["recorded"] = None  # a derived fixture's outcome is what the replay computes
        path = FIXTURES / f"{name}.json"
        path.write_text(json.dumps(data, indent=1, sort_keys=True, default=str) + "\n", "utf-8")
        out.append(path)
    return out


def main(argv: list[str] | None = None) -> int:
    from extant.index.settings import load_index_settings

    parser = argparse.ArgumentParser(prog="python -m extant.agent.record")
    parser.add_argument("--schema", required=True)
    parser.add_argument("--only", help="comma separated spec names")
    args = parser.parse_args(argv)
    wanted = set(args.only.split(",")) if args.only else {s.name for s in SPECS}
    FIXTURES.mkdir(parents=True, exist_ok=True)
    dsn = load_index_settings().writer_dsn.get_secret_value()
    with psycopg.connect(dsn, autocommit=True) as conn:
        for spec in SPECS:
            if spec.name not in wanted:
                continue
            data = _record(spec, conn, args.schema)
            path = FIXTURES / f"{spec.name}.json"
            path.write_text(json.dumps(data, indent=1, sort_keys=True, default=str) + "\n", "utf-8")
            r = data["recorded"]
            cost = sum(n["eur"] for n in r["node_log"])
            print(
                f"{spec.name}: {r['path']} -> {r['outcome']} {r['refusal_reason'] or ''}"
                f" {r['caveats']} EUR {cost:.5f}"
            )
    for path in derive():
        print(f"derived {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
