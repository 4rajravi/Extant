"""Layer boundaries, enforced by tests rather than by discipline."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

RBI = Path(__file__).resolve().parents[2] / "extant" / "corpora" / "rbi"

# Model and embedding clients, and local model runtimes. Phase 1 calls none of them.
MODEL_CLIENTS = {
    "anthropic", "openai", "cohere", "voyageai", "mistralai", "google.generativeai",
    "google.genai", "vertexai", "litellm", "langchain", "langchain_core", "langchain_openai",
    "llama_index", "transformers", "sentence_transformers", "torch", "onnxruntime",
    "tiktoken", "ollama", "groq", "together", "replicate", "huggingface_hub",
    "pymupdf4llm", "pymupdf.layout",
}  # fmt: skip


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _hits(modules: set[str], banned: set[str]) -> set[str]:
    return {m for m in modules for b in banned if m == b or m.startswith(b + ".")}


@pytest.mark.parametrize("path", sorted(RBI.rglob("*.py")), ids=lambda p: p.name)
def test_no_model_client_is_imported(path: Path) -> None:
    assert not _hits(imported_modules(path), MODEL_CLIENTS)


def test_no_model_client_is_loaded_at_runtime() -> None:
    """Import every RBI module in a fresh interpreter and check nothing pulled a model
    client in transitively."""
    modules = sorted(
        "extant.corpora.rbi." + p.stem for p in RBI.glob("*.py") if p.stem != "__init__"
    )
    probe = (
        "import importlib, sys\n"
        f"for m in {modules!r}: importlib.import_module(m)\n"
        f"banned = {sorted(MODEL_CLIENTS)!r}\n"
        "print(sorted(m for m in sys.modules for b in banned if m == b or m.startswith(b + '.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


@pytest.mark.parametrize("name", ["acquisition.py", "importer.py"])
def test_acquisition_never_parses(name: str) -> None:
    assert not _hits(
        imported_modules(RBI / name),
        {"selectolax", "pymupdf", "fitz", "pdfplumber", "bs4", "lxml"},
    )


def test_import_is_offline() -> None:
    assert not _hits(imported_modules(RBI / "importer.py"), {"httpx", "requests", "urllib"})


def test_discovery_and_crawl_never_extract_pdfs() -> None:
    for name in ("sources.py", "crawl.py"):
        assert not _hits(imported_modules(RBI / name), {"pymupdf", "fitz", "pdfplumber"})


@pytest.mark.parametrize(
    "name",
    [
        "extract.py",
        "extract_html.py",
        "lists.py",
        "mine.py",
        "identifiers.py",
        "entities.py",
        "parse.py",
        "parse_run.py",
        "anchors.py",
        "report.py",
        "chunk.py",
    ],
)
def test_offline_steps_import_no_http_client(name: str) -> None:
    assert not _hits(imported_modules(RBI / name), {"httpx", "requests", "urllib"})
