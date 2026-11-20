"""Steps 6 to 8 for the RBI corpus: the inputs a build loads, and its manifest.

The chunks come from step 5 (``chunk.py``); documents, paragraphs and facets from step 3; the
entity taxonomy from step 4; concordance and withdrawals from the committed gold label sets in
``evals/labels/rbi/``, whose manifest must name the current extractor, parser and miner
versions. The manifest written into each build records every version, the git commit, each
document's ``source_sha256`` and the baseline it was verified against.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from extant.core.models import (
    ConcordanceEntry,
    Document,
    EntityType,
    Facet,
    Paragraph,
    Withdrawal,
)
from extant.corpora.rbi.chunk import CHUNKER_VERSION, load_chunks
from extant.corpora.rbi.extract_html import HTML_EXTRACTOR_VERSION
from extant.corpora.rbi.mine import MINER_VERSION
from extant.corpora.rbi.parse import PARSER_VERSION
from extant.index.assemble import BuildInputs
from extant.index.chunking import ChunkDraft

REPO = Path(__file__).resolve().parents[3]
LABELS = REPO / "evals" / "labels" / "rbi"
BASELINE = REPO / "evals" / "baseline-counts.json"


class BuildInputError(RuntimeError):
    pass


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def code_versions() -> dict[str, str]:
    return {
        "html_extractor_version": HTML_EXTRACTOR_VERSION,
        "parser_version": PARSER_VERSION,
        "miner_version": MINER_VERSION,
        "chunker_version": CHUNKER_VERSION,
    }


def git_state(repo: Path = REPO) -> tuple[str, bool]:
    """The short commit and whether the working tree has uncommitted changes."""

    def git(*args: str) -> str:
        # A fixed argv: git from PATH, arguments from this module only.
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    return git("rev-parse", "--short=7", "HEAD"), bool(git("status", "--porcelain"))


def texts_to_embed(chunks: Sequence[ChunkDraft], *, sample: int | None = None) -> dict[str, str]:
    """content_hash -> embed_text; with ``sample``, only the first N documents' chunks."""
    if sample is not None:
        keep = set(sorted({c.doc_id for c in chunks})[:sample])
        chunks = [c for c in chunks if c.doc_id in keep]
    return {c.content_hash: c.embed_text for c in chunks}


def _documents(path: Path) -> tuple[list[Document], int]:
    """The documents, with each ``supersedes_doc_id`` pointing at the nearest earlier version
    that has a document row: a version published only as PDF has none, and the foreign key
    needs one. Returns how many links were moved."""
    docs = {d.doc_id: d for d in map(Document.model_validate, _jsonl(path))}
    moved = 0
    out = []
    for d in docs.values():
        target = d.supersedes_doc_id
        if target is not None and target not in docs:
            base, version = target.rsplit(":v", 1)
            target = next(
                (f"{base}:v{v}" for v in range(int(version) - 1, 0, -1) if f"{base}:v{v}" in docs),
                None,
            )
            d = d.model_copy(update={"supersedes_doc_id": target})
            moved += 1
        out.append(d)
    return out, moved


def _labels() -> tuple[list[ConcordanceEntry], list[Withdrawal]]:
    manifest = json.loads((LABELS / "MANIFEST.json").read_text(encoding="utf-8"))
    pinned = {
        "html_extractor": HTML_EXTRACTOR_VERSION,
        "parser": f"p{PARSER_VERSION}",
        "miner": f"m{MINER_VERSION}",
    }
    stale = {k: manifest.get(k) for k, v in pinned.items() if manifest.get(k) != v}
    if stale:
        raise BuildInputError(
            f"evals/labels/rbi was written by {stale}, not the current {pinned}; rerun report"
        )
    concordance = [
        ConcordanceEntry.model_validate(r) for r in _jsonl(LABELS / "supersession.jsonl")
    ]
    withdrawals = [
        Withdrawal.model_validate(r)
        for name in ("withdrawals.jsonl", "withdrawals_supervision.jsonl")
        for r in _jsonl(LABELS / name)
    ]
    return concordance, withdrawals


def load_inputs(data_dir: Path, *, git_commit: str, git_dirty: bool) -> BuildInputs:
    derived = data_dir / "derived"
    documents, moved = _documents(derived / "parsed" / "documents.jsonl")
    concordance, withdrawals = _labels()
    manifest: dict[str, Any] = {
        **code_versions(),
        "git_dirty": git_dirty,
        "built_from_commit": git_commit,
        "source_sha256": {d.doc_id: d.source_sha256 for d in documents},
        "supersedes_moved_past_pdf_only_versions": moved,
        "eval_baseline": "evals/baseline-counts.json",
        "label_sets": "evals/labels/rbi/MANIFEST.json",
    }
    return BuildInputs(
        documents=documents,
        paragraphs=[
            Paragraph.model_validate(r) for r in _jsonl(derived / "parsed/paragraphs.jsonl")
        ],
        facets=[Facet.model_validate(r) for r in _jsonl(derived / "parsed/facets.jsonl")],
        entity_types=[EntityType.model_validate(r) for r in _jsonl(derived / "entity_types.jsonl")],
        chunks=load_chunks(data_dir),
        concordance=concordance,
        withdrawals=withdrawals,
        manifest=manifest,
    )
