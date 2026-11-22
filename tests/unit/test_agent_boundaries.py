"""Who may touch the database, and who may call a model.

Only ``retrieve`` and ``resolve_reference`` read the database; only ``parse``, ``compose`` and
``judge`` call a model. Held three ways: by import, by what a fresh interpreter loads, and by
what each node is handed at run time.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from extant.agent.runtime import NODES, Deps, given
from tests.agent_support import ScriptedIndex, parse_says
from tests.unit.test_rbi_boundaries import imported_modules

NODE_DIR = Path(__file__).resolve().parents[2] / "extant" / "agent" / "nodes"
READERS = {"retrieve", "resolve_reference"}
DATABASE = {
    "psycopg",
    "extant.retrieval",
    "extant.agent.index",
    "extant.core.schema",
    "extant.index",
}
MODEL_RUNTIMES = {
    "langchain",
    "langchain_core",
    "langchain_anthropic",
    "anthropic",
    "openai",
    "httpx",
}


def _hits(path: Path, banned: set[str]) -> set[str]:
    return {m for m in imported_modules(path) for b in banned if m == b or m.startswith(b + ".")}


@pytest.mark.parametrize("name", sorted(set(NODES) - READERS))
def test_no_other_node_imports_the_database_layer(name: str) -> None:
    assert not _hits(NODE_DIR / f"{name}.py", DATABASE)


@pytest.mark.parametrize("name", NODES)
def test_no_node_imports_a_model_client_directly(name: str) -> None:
    assert not _hits(NODE_DIR / f"{name}.py", MODEL_RUNTIMES)


def test_importing_the_deterministic_nodes_loads_no_database_driver() -> None:
    modules = [f"extant.agent.nodes.{n}" for n in sorted(set(NODES) - READERS)]
    probe = (
        "import importlib, sys\n"
        f"for m in {modules!r}: importlib.import_module(m)\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'psycopg'"
        " or m in ('extant.retrieval', 'extant.agent.index')))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_each_node_is_handed_only_what_it_may_use() -> None:
    deps = Deps(
        models={"parse": parse_says(), "compose": parse_says(), "judge": parse_says()},
        index=ScriptedIndex(),
    )
    for name in NODES:
        g = given(name, deps)
        assert (g.index is not None) == (name in READERS), name
        assert (g.model is not None) == (name in {"parse", "compose", "judge"}), name
