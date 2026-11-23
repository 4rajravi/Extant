"""The eval harness sits outside serving: nothing in ``extant`` imports ``evals``.

This is the boundary that rots first, when someone wants to reuse a metric at runtime, so it
is held by a test rather than by a convention.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_rbi_boundaries import imported_modules

REPO = Path(__file__).resolve().parents[2]
SERVING = sorted((REPO / "extant").rglob("*.py"))
HARNESS = sorted(p for p in (REPO / "evals").rglob("*.py") if "ablation" not in p.parts)


@pytest.mark.parametrize("path", SERVING, ids=lambda p: str(p.relative_to(REPO)))
def test_no_serving_module_imports_the_harness(path: Path) -> None:
    hits = {m for m in imported_modules(path) if m == "evals" or m.startswith("evals.")}
    assert not hits, f"{path} imports {hits}"


def test_no_serving_module_names_the_harness_dynamically() -> None:
    for path in SERVING:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
        assert not {v for v in literals if isinstance(v, str) and v.startswith("evals.")}, path


# The probe imports extant.core.settings, which by design refuses to import without its
# required settings (Phase 0). CI sets only the database DSNs and has no .env, so the probe
# is given throwaway values for the rest; anything the environment already sets wins. None
# of them is used: nothing connects, embeds or calls a model on import.
_THROWAWAY = {
    "EXTANT_WRITER_DSN": "postgresql://extant:unused@localhost:1/extant",
    "EXTANT_READER_DSN": "postgresql://extant_reader:unused@localhost:1/extant",
    "EXTANT_BUILD_SCHEMA": "idx_rbi_20990101_abcdef1",
    "EXTANT_EMBEDDING_PROVIDER": "openai",
    "EXTANT_EMBEDDING_MODEL": "text-embedding-3-large",
    "EXTANT_LLM_PROVIDER": "anthropic",
    "EXTANT_ROUTER_MODEL": "claude-haiku-5-5",
    "EXTANT_COMPOSER_MODEL": "claude-opus-5-5",
}


def test_importing_every_serving_module_loads_no_part_of_the_harness(tmp_path: Path) -> None:
    modules = sorted(
        ".".join(p.relative_to(REPO).with_suffix("").parts)
        for p in SERVING
        if p.name != "__main__.py"
    )
    probe = (
        "import importlib, sys\n"
        f"for m in {modules!r}: importlib.import_module(m.removesuffix('.__init__'))\n"
        "print(sorted(m for m in sys.modules if m == 'evals' or m.startswith('evals.')))"
    )
    # Run away from the repository's .env, as CI does, so a local .env cannot hide a module
    # that fails to import without one.
    env = {**_THROWAWAY, **os.environ, "PYTHONPATH": str(REPO)}
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=tmp_path, env=env
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "[]"


def test_the_gate_path_loads_no_local_model_runtime() -> None:
    """CI never installs the ablation group, so the runner, gate and renderer must import
    without it."""
    probe = (
        "import sys, evals.retrieval, evals.gate, evals.render, evals.baseline, evals.audit\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in"
        " {'torch', 'sentence_transformers', 'transformers'}))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


@pytest.mark.parametrize("path", HARNESS, ids=lambda p: p.name)
def test_the_harness_never_calls_an_embedding_api_directly(path: Path) -> None:
    """Only ``evals.query_vectors --refresh`` may embed, through extant's embedder; no
    harness module imports an HTTP or model client of its own."""
    banned = {"httpx", "requests", "urllib.request", "aiohttp", "openai"}
    assert not {
        m for m in imported_modules(path) for b in banned if m == b or m.startswith(b + ".")
    }
