"""The answer level harness (Phase 6): metrics, set validation, cassettes, the dry run, the
ablation arms and the blind label file. No test here calls a model or spends anything."""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from evals.answers import judge as judge_mod
from evals.answers import run as run_mod
from evals.answers.metrics import agreement, citation_validity, refusal_pair
from evals.answers.sets import SetValidationError, load, validate
from extant.agent.arms import Arm
from extant.agent.graph import ask, build
from extant.agent.llm import FakeModel, call_key
from extant.agent.models import (
    AgentState,
    ComposerClaim,
    ComposerOutput,
    JudgeOutput,
    RefusalReason,
)
from extant.agent.runtime import Deps
from tests.agent_support import ENTITIES, TODAY, ScriptedIndex, cand, judge_says, parse_says

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- metrics


def _row(qid: str, outcome: str, *cites: tuple[str, int, int], **kw: Any) -> dict[str, Any]:
    claims = (
        [
            {
                "text": "x",
                "citations": [
                    {"para_id": p, "quote_start": s, "quote_end": e} for p, s, e in cites
                ],
                "quotes": ["capital ratio"],
            }
        ]
        if cites
        else []
    )
    return {"qid": qid, "outcome": outcome, "claims": claims, **kw}


BODIES = {"rbi:md:2025:0001:v1#p1": "A bank shall keep a capital ratio of 9 per cent."}


def test_citation_validity_is_zero_on_a_clean_run_and_catches_a_planted_citation() -> None:
    clean = [_row("q1", "answer", ("rbi:md:2025:0001:v1#p1", 20, 33))]
    assert citation_validity(clean, set(BODIES), BODIES).violations == 0
    planted = [*clean, _row("q2", "answer", ("rbi:md:2025:9999:v1#p404", 0, 5))]
    check = citation_validity(planted, set(BODIES), BODIES)
    assert check.violations == 1
    assert "p404" in check.examples[0]


def test_a_citation_whose_paragraph_holds_none_of_the_quotes_is_counted_separately() -> None:
    row = _row("q1", "answer", ("rbi:md:2025:0001:v1#p1", 0, 0))
    assert citation_validity([row], set(BODIES), BODIES).quote_violations == 0  # it is there
    row["claims"][0]["quotes"] = ["a sentence the paragraph never says"]
    check = citation_validity([row], set(BODIES), BODIES)
    assert (check.violations, check.quote_violations) == (0, 1)


def test_the_refusal_pair_is_computed_together_or_not_at_all() -> None:
    answerable = [{"outcome": "answer"}, {"outcome": "refusal"}, {"outcome": "answer"}]
    unanswerable = [
        {"outcome": "refusal", "reason": "no_support_found", "expect": "no_support_found"},
        {"outcome": "answer", "reason": None, "expect": "no_support_found"},
    ]
    pair = refusal_pair(answerable, unanswerable)
    assert (pair.refused, pair.unanswerable, pair.refused_for_expected_reason) == (1, 2, 1)
    assert (pair.false_refusals, pair.answerable) == (1, 3)
    assert pair.correctness.value == 0.5
    assert pair.false_refusal_rate.value == pytest.approx(1 / 3)
    with pytest.raises(ValueError, match="both"):
        refusal_pair(answerable, [])
    with pytest.raises(ValueError, match="both"):
        refusal_pair([], unanswerable)


def test_judge_agreement_against_a_hand_built_fixture() -> None:
    # judge, person: agree on 3 of 4; judge says supported half the time, the person a quarter
    a = agreement([(True, True), (True, False), (False, False), (False, False)])
    assert (a.agree, a.n) == (3, 4)
    assert a.rate.value == 0.75
    assert a.kappa == pytest.approx(0.5)


# --------------------------------------------------------------------------- the sets


UNANSWERABLE = {
    "qid": "u99",
    "set": "unanswerable",
    "as_of": "2026-11-20",
    "source": "absent",
    "why": "the corpus holds no provision on this topic",
    "evidence": {"in_scope_in_force": 0, "top_retrieved": []},
}


def test_an_unanswerable_entry_without_its_reason_or_evidence_fails_validation() -> None:
    validate(UNANSWERABLE)
    with pytest.raises(SetValidationError, match="reason"):
        validate({**UNANSWERABLE, "why": ""})
    with pytest.raises(SetValidationError, match="assertion"):
        validate({**UNANSWERABLE, "evidence": None})
    with pytest.raises(SetValidationError, match="does not bear out"):
        validate({**UNANSWERABLE, "evidence": {"in_scope_in_force": 2, "top_retrieved": []}})


def test_the_committed_sets_all_validate_and_are_never_pooled() -> None:
    sizes = {name: len(load(name)) for name in ("answerable", "as_of", "unanswerable")}
    assert sizes == {"answerable": 56, "as_of": 10, "unanswerable": 37}
    splits = [r["split"] for r in load("answerable")]
    assert splits.count("dev") == splits.count("test")
    path = REPO / "evals" / "answers" / "sets" / "removed.jsonl"
    removed = [json.loads(x) for x in path.read_text().splitlines()]
    assert {r["qid"] for r in removed} == {"u05", "u32", "u33"}
    assert all(r["removed"] for r in removed)


# --------------------------------------------------------------------------- cassettes


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network call")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_cassette_replay_makes_zero_network_calls(tmp_path: Path, no_network: None) -> None:
    store = run_mod.CassetteStore(tmp_path / "models.jsonl")
    key = call_key(JudgeOutput, "s", "u")
    store.put(
        {
            "key": key,
            "schema": "JudgeOutput",
            "model": "claude-haiku-5-5",
            "recorded_on": "2026-11-23",
            "input_tokens": 10,
            "output_tokens": 1,
            "latency_ms": 5.0,
            "parsed": {"supported": True},
        }
    )
    model = run_mod.CassetteModel("claude-haiku-5-5", run_mod.CassetteStore(store.path), None)
    done = model.complete(JudgeOutput, system="s", user="u")
    assert done.parsed == JudgeOutput(supported=True)
    assert (model.replayed, model.fresh, model.spent_eur) == (1, 0, 0.0)
    with pytest.raises(run_mod.CassetteMissError):
        model.complete(JudgeOutput, system="s", user="something not recorded")


def test_a_new_call_is_recorded_with_its_model_and_date(tmp_path: Path) -> None:
    store = run_mod.CassetteStore(tmp_path / "models.jsonl")
    inner = judge_says(True)
    model = run_mod.CassetteModel("claude-haiku-5-5", store, inner)
    model.complete(JudgeOutput, system="s", user="u")
    model.complete(JudgeOutput, system="s", user="u")  # the second is a replay
    (entry,) = [json.loads(x) for x in store.path.read_text().splitlines()]
    assert entry["model"] == "claude-haiku-5-5"
    assert entry["recorded_on"] == run_mod.EVAL_TODAY.isoformat()
    assert (len(inner.calls), model.fresh, model.replayed) == (1, 1, 1)


def test_the_dry_run_makes_no_model_embedding_or_database_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_network: None
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the dry run touched a model or the database")

    monkeypatch.setattr("extant.agent.settings.LangChainModel", forbidden)
    monkeypatch.setattr("psycopg.connect", forbidden)
    assert run_mod.main(["plan", "--schema", "idx_rbi_20261123_c23c1e8"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"= EUR \d+\.\d\d", out)
    assert "planned (arm x question): 412" in out
    assert "hard stop" in out


# --------------------------------------------------------------------------- the arms

BODY = "A bank shall maintain a minimum capital ratio of 9 per cent of its risk weighted assets."
GOOD = ComposerClaim(
    text="A bank must keep a minimum capital ratio of 9 per cent.",
    candidate_indices=[0],
    quotes=["minimum capital ratio of 9 per cent"],
)
BAD = GOOD.model_copy(update={"quotes": ["a quote the paragraph does not contain"]})


def _composer(*outputs: ComposerClaim) -> FakeModel:
    queue = list(outputs)

    def respond(schema: type[BaseModel], s: str, u: str) -> BaseModel:
        return ComposerOutput(claims=[queue.pop(0) if len(queue) > 1 else queue[0]])

    return FakeModel(respond, name="claude-opus-5-5")


def _run(arm: Arm, index: ScriptedIndex, composer: FakeModel) -> AgentState:
    deps = Deps(
        models={"parse": parse_says(), "compose": composer, "judge": judge_says()},
        index=index,
        entities=ENTITIES,
        arm=arm,
    )
    return ask(
        build(deps),
        "What capital ratio must a bank keep?",
        index_version="idx_rbi_20261123_c23c1e8",
        entity="cb",
        as_of=TODAY,
        today=TODAY,
        supersession=arm.supersession,
    )


def test_without_supersession_a_repealed_paragraph_is_answered_from() -> None:
    from datetime import date

    stale = cand(1, BODY, valid_to=date(2026, 1, 1), origin="probe")
    baseline = _run(Arm(), ScriptedIndex(probe=[stale]), _composer(GOOD))
    assert baseline.refusal_reason is RefusalReason.SUPERSEDED_ONLY
    arm = _run(
        Arm("no_supersession", supersession=False), ScriptedIndex(probe=[stale]), _composer(GOOD)
    )
    assert arm.outcome == "answer"
    assert [c.para_id for c in arm.verified_claims[0].citations] == [stale.para_id]


def test_without_verification_a_misquoted_claim_becomes_an_answer() -> None:
    index = ScriptedIndex(ranked=[cand(1, BODY)])
    baseline = _run(Arm(), index, _composer(BAD))
    assert baseline.refusal_reason is RefusalReason.VERIFICATION_FAILED
    arm = _run(Arm("no_verification", verification=False), index, _composer(BAD))
    assert arm.outcome == "answer"
    citation = arm.verified_claims[0].citations[0]
    assert (citation.quote_start, citation.quote_end) == (0, 0)  # quote not found, not invented


def test_the_retry_arm_recovers_a_misquote_with_one_more_composer_call() -> None:
    index = ScriptedIndex(ranked=[cand(1, BODY)])
    composer = _composer(BAD, GOOD)
    state = _run(Arm("retry", retry=True), index, composer)
    assert state.outcome == "answer"
    assert len(composer.calls) == 2
    assert "failed verification" in composer.calls[1][2]  # the failure went back as context
    assert state.checks["retry"].passed


def test_the_retry_arm_does_not_retry_what_rewording_cannot_fix() -> None:
    from datetime import date

    stale = cand(1, BODY, valid_to=date(2026, 1, 1))
    composer = _composer(GOOD)
    state = _run(Arm("retry", retry=True), ScriptedIndex(ranked=[stale]), composer)
    assert state.outcome == "refusal"
    assert len(composer.calls) == 0  # the gate refused before compose


def test_nothing_in_serving_builds_an_arm_other_than_the_default() -> None:
    hits = []
    for path in (REPO / "extant").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"\bArm\(", line) and "class Arm" not in line:
                hits.append(f"{path.relative_to(REPO)}: {line.strip()}")
    assert hits == ["extant/agent/arms.py: BASELINE = Arm()"]


# --------------------------------------------------------------------------- the label file


def _judged(n: int) -> list[dict[str, Any]]:
    return [
        {
            "arm": "baseline",
            "qid": f"a{i:02d}",
            "set": "answerable",
            "split": "dev",
            "claim": 0,
            "pair": f"{i:012x}",
            "overlap": 0.5 if i % 2 else 0.9,
            "supported": i % 3 != 0,
            "user": f"Claim: claim {i}\n\nParagraphs:\nparagraph {i}",
        }
        for i in range(n)
    ]


def test_the_label_file_is_blind_and_agreement_waits_for_every_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    rows: Sequence[dict[str, Any]] = _judged(60)
    (run_dir / "judged.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(judge_mod, "RUNS", tmp_path)
    out = judge_mod.draw("r1", tmp_path / "labels.md")
    text = out.read_text()
    assert text.count("**Human verdict:**") == 50
    assert "Judge verdict" not in text
    assert "overlap 0." not in text
    assert judge_mod.measure(out) is None  # nothing labelled yet
    key = json.loads(out.with_suffix(".key.json").read_text())
    filled = text
    for pid in re.findall(r"^## \d+\. ([0-9a-f]{12})$", text, re.M):
        verdict = "supported" if key[pid]["supported"] else "unsupported"
        filled = filled.replace("**Human verdict:** \n", f"**Human verdict:** {verdict}\n", 1)
    out.write_text(filled)
    a = judge_mod.measure(out)
    assert a is not None
    assert (a.agree, a.n) == (50, 50)
