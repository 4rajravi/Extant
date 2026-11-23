"""Cached example answers: rungs 4 and 5 of the degradation ladder, and the cap.

Three situations serve these instead of a live answer: the daily cap is reached, Postgres is
unreachable after one retry, or (with no cache at all) the static page. Each example is a
real answer the graph produced, kept with the build it was computed on, so a stale cache is
detectable: ``stale`` compares that build with the one being served, and readiness reports
the mismatch.

    uv run python -m extant.agent.examples build      # from the recorded agent fixtures

The cache is built from the recorded fixtures (``tests/fixtures/agent``) rather than by
asking the models again: those are real answers to real questions on the current build,
already paid for, and re-recording them is what refreshes this cache too.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from extant.agent.models import AskResponse, CachedExample
from extant.guards import pii

CACHE = Path(__file__).resolve().parent / "cached_examples.json"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "agent"

# Rung 5: what the system says when it can show nothing at all.
STATIC_PAGE = (
    "Extant answers questions about Reserve Bank of India directions: which provision governs"
    " a question, as of which date, and for which kind of regulated entity, with every claim"
    " quoted from the provision it relies on. It is not answering right now because a service"
    " it depends on is unavailable, and it will not guess in the meantime. Please try again"
    " later, and check the RBI's own Master Directions for anything you need today."
)


class ExampleCacheError(RuntimeError):
    """The cache file is missing or does not validate: serve the static page."""


@dataclass(frozen=True)
class ExampleCache:
    index_version: str
    built_on: date
    examples: tuple[CachedExample, ...]

    def stale(self, serving: str) -> bool:
        return self.index_version != serving

    @classmethod
    def load(cls, path: Path = CACHE) -> ExampleCache:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            examples = tuple(CachedExample.model_validate(e) for e in data["examples"])
            cache = cls(data["index_version"], date.fromisoformat(data["built_on"]), examples)
        except (OSError, KeyError, ValueError, ValidationError) as exc:
            raise ExampleCacheError(f"no usable example cache at {path}: {exc}") from exc
        if not examples or any(e.index_version != cache.index_version for e in examples):
            raise ExampleCacheError(f"{path} mixes builds or holds no examples")
        return cache


def from_fixtures(fixtures: Path = FIXTURES) -> list[CachedExample]:
    """Every recorded fixture that answered an ordinary question becomes an example. Left
    out: derived fixtures, whose answers come from a changed copy of the corpus, and the
    adversarial cases (``adv_``), which are tests, not examples a user should be shown. A
    question carrying an identifier is refused outright: these are shown to every user."""
    out = []
    for path in sorted(fixtures.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        recorded = data.get("recorded")
        if path.stem.startswith("adv_") or data.get("derived_from"):
            continue
        if not recorded or recorded["outcome"] != "answer":
            continue
        if pii.found(data["spec"]["question"]):
            raise ExampleCacheError(f"{path.name}: an example question carries an identifier")
        response = AskResponse.model_validate(recorded["response"])
        if response.result.outcome != "answer":
            continue
        out.append(
            CachedExample(
                question=data["spec"]["question"],
                entity_type=response.entity_type,
                as_of=response.as_of,
                index_version=response.index_version,
                answer=response.result,
            )
        )
    return out


def write(examples: list[CachedExample], built_on: date, path: Path = CACHE) -> Path:
    versions = {e.index_version for e in examples}
    if len(versions) != 1:
        raise ExampleCacheError(f"examples come from {len(versions)} builds: {sorted(versions)}")
    payload = {
        "index_version": versions.pop(),
        "built_on": built_on.isoformat(),
        "examples": [e.model_dump(mode="json") for e in examples],
    }
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m extant.agent.examples")
    parser.add_argument("command", choices=["build"])
    parser.parse_args(argv)
    examples = from_fixtures()
    path = write(examples, date.today())
    print(f"wrote {len(examples)} examples on {examples[0].index_version} to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
