"""The committed example cache: loads, comes from one build, and shows nobody's identifiers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from extant.agent.examples import ExampleCache, ExampleCacheError, from_fixtures
from extant.guards.pii import found
from tests.replay_support import FIXTURES


def test_the_committed_cache_loads_from_one_build_with_no_identifiers() -> None:
    cache = ExampleCache.load()
    assert len(cache.examples) >= 3
    assert {e.index_version for e in cache.examples} == {cache.index_version}
    text = json.dumps([e.model_dump(mode="json") for e in cache.examples], ensure_ascii=False)
    assert found(text) == []


def test_the_cache_is_built_from_ordinary_answered_fixtures_only() -> None:
    names = {e.question for e in from_fixtures()}
    adversarial = {
        json.loads(p.read_text(encoding="utf-8"))["spec"]["question"]
        for p in FIXTURES.glob("adv_*.json")
    }
    assert names
    assert not names & adversarial


def test_a_question_with_an_identifier_is_never_cached(tmp_path: Path) -> None:
    src = json.loads((FIXTURES / "full_answer_third_person.json").read_text(encoding="utf-8"))
    src["spec"]["question"] = "My PAN is ABCPE1234F. " + src["spec"]["question"]
    (tmp_path / "leaky.json").write_text(json.dumps(src), encoding="utf-8")
    with pytest.raises(ExampleCacheError, match="identifier"):
        from_fixtures(tmp_path)


def test_a_missing_or_broken_cache_is_an_error_the_service_turns_into_the_static_page(
    tmp_path: Path,
) -> None:
    with pytest.raises(ExampleCacheError):
        ExampleCache.load(tmp_path / "absent.json")
    (tmp_path / "broken.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ExampleCacheError):
        ExampleCache.load(tmp_path / "broken.json")
