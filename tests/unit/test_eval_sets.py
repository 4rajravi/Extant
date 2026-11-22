"""The committed eval sets: deterministic splits, no leakage across them, and tamper evident."""

from __future__ import annotations

import shutil
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pytest

from evals import sets
from evals.sets import assign_split, first_sentence, load_sets


def test_splits_are_a_deterministic_function_of_the_group() -> None:
    assert assign_split("direction:1") == assign_split("direction:1")
    halves = [assign_split(f"g{i}") for i in range(2000)]
    assert 900 < halves.count("dev") < 1100


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("7. The bank shall keep records. It shall also report.", "The bank shall keep records."),
        ("(a) where the NBFC is a company; and", "where the NBFC is a company; and"),
        ("iii) Rs. 5 lakh applies here", "Rs. 5 lakh applies here"),
    ],
)
def test_first_sentence_drops_the_paragraph_number(body: str, expected: str) -> None:
    assert first_sentence(body) == expected


def test_no_group_straddles_dev_and_test() -> None:
    for eval_set in load_sets().values():
        by_group: dict[str, set[str]] = defaultdict(set)
        for q in eval_set.queries:
            assert q.split == assign_split(q.group), q.query_id
            by_group[q.group].add(q.split)
        assert all(len(s) == 1 for s in by_group.values()), eval_set.name


def test_temporal_pairs_ask_the_same_question_either_side_of_the_boundary() -> None:
    temporal = load_sets(["temporal"])["temporal"]
    pairs: dict[str, dict[str, sets.EvalQuery]] = defaultdict(dict)
    for q in temporal.queries:
        stem, side = q.query_id.rsplit("-", 1)
        pairs[stem][side] = q
    complete = [p for p in pairs.values() if set(p) == {"old", "new"}]
    assert complete
    for p in complete:
        old, new = p["old"], p["new"]
        assert old.text == new.text
        assert old.as_of + timedelta(days=1) == new.as_of
        assert old.gold_para_ids != new.gold_para_ids
        assert old.gold_para_ids[0].split("#")[1] == new.gold_para_ids[0].split("#")[1]


def test_the_holdout_is_exactly_the_material_after_the_cutoff() -> None:
    for eval_set in load_sets().values():
        for q in eval_set.queries:
            assert q.holdout == (q.published_on > sets.HOLDOUT_CUTOFF)


def test_a_hand_edited_set_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy = tmp_path / "rbi"
    shutil.copytree(sets.SETS_DIR, copy)
    monkeypatch.setattr(sets, "SETS_DIR", copy)
    assert load_sets(["concordance"])
    target = copy / "concordance.p2.jsonl"
    target.write_text(target.read_text(encoding="utf-8").replace("replaced", "superseded"), "utf-8")
    with pytest.raises(sets.StaleSetError, match="hand edit"):
        load_sets(["concordance"])


def test_sets_built_from_other_label_versions_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newer = {"html_extractor": "html-3", "parser": "p3", "miner": "m1"}
    monkeypatch.setattr(sets, "label_versions", lambda: newer)
    with pytest.raises(sets.StaleSetError, match="other label versions"):
        load_sets()
