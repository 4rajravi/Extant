"""Step 5: chunk rows from the parsed paragraphs. Offline and fully deterministic.

One chunk per paragraph, carrying every filter and ranking field so the hot table answers a
query without a join: ``doc_id``, ``ordinal``, ``entity_codes``, ``valid_from``, ``valid_to``
and ``authority_rank`` are copied onto each chunk from its document and facets. A paragraph
over ``MAX_CHUNK_TOKENS`` is split by ``extant.index.chunking.split_body`` into pieces that
share its ``para_id`` and differ by ``sub_index``.

Entity scope: the codes the direction's applicability paragraph brings into scope (the
``applies_to`` facets, in the parser's order), or, for a direction whose applicability text
names none, the category RBI lists it under.

Validity, the half-open interval ``[valid_from, valid_to)``: a version is in force from its
start until the next version's start, and the current version has no end. A first version
starts on its ``effective_from`` when the text dates it, else on its issue date, else on its
"Updated as on" date; a later version starts on its "Updated as on" date (RBI's own date for
the consolidated text), else its issue date. Versions with no text (PDF only) still end their
predecessor's window, so the index has a gap there rather than a superseded text in force.
None of these dates is part of ``embed_text``, so changing the rule costs no re-embedding.

Cached on the parsed inputs plus ``CHUNKER_VERSION``:
``derived/chunks/c<CHUNKER_VERSION>-<inputs digest>/chunks.jsonl`` (and ``summary.json``).
Identical inputs and version give byte-identical output.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from extant.core.embedding import compose_embed_text
from extant.core.models import Document, Paragraph
from extant.index.chunking import ChunkDraft, make_draft, split_body

# Bump on any change to this module or to extant.index.chunking that changes the rows.
CHUNKER_VERSION = "1"

_INPUTS = (
    "parsed/documents.jsonl",
    "parsed/paragraphs.jsonl",
    "parsed/facets.jsonl",
    "parsed/parse_report.jsonl",
    "versions.jsonl",
    "entity_types.jsonl",
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run parse and mine first")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def inputs_digest(data_dir: Path) -> str:
    h = hashlib.sha256(f"chunker {CHUNKER_VERSION}\n".encode())
    for name in _INPUTS:
        path = data_dir / "derived" / name
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing; run parse and mine first")
        h.update(name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def chunks_dir(data_dir: Path) -> Path:
    return data_dir / "derived" / "chunks" / f"c{CHUNKER_VERSION}-{inputs_digest(data_dir)[:16]}"


def chunk_paragraphs(
    document: Document,
    paragraphs: Sequence[Paragraph],
    *,
    entity_codes: Sequence[str],
    entity_labels: Sequence[str],
    valid_from: date,
    valid_to: date | None,
) -> list[ChunkDraft]:
    """Chunk rows for one document's paragraphs, in ordinal order."""
    drafts: list[ChunkDraft] = []
    for p in sorted(paragraphs, key=lambda p: p.ordinal):
        for sub_index, piece in enumerate(split_body(p.body)):
            drafts.append(
                make_draft(
                    para_id=p.para_id,
                    sub_index=sub_index,
                    doc_id=p.doc_id,
                    ordinal=p.ordinal,
                    body=piece,
                    embed_text=compose_embed_text(document, p, entity_labels, body=piece),
                    entity_codes=entity_codes,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    authority_rank=int(document.authority_rank),
                )
            )
    return drafts


def validity_windows(
    versions: Sequence[dict[str, Any]], report: dict[str, dict[str, Any]]
) -> dict[str, tuple[date, date | None]]:
    """``[valid_from, valid_to)`` for every version, by doc_id (rule in the module docstring)."""

    def day(value: str | None) -> date | None:
        return date.fromisoformat(value) if value else None

    by_direction: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for v in versions:
        by_direction[v["direction_id"]].append(v)
    windows: dict[str, tuple[date, date | None]] = {}
    for did, chain in sorted(by_direction.items()):
        chain.sort(key=lambda v: v["version"])
        starts: list[date] = []
        for v in chain:
            r = report[v["doc_id"]]
            order = (
                (r["effective_from"], r["issued_on"], v["updated_as_on"])
                if v["version"] == 1
                else (v["updated_as_on"], r["issued_on"], r["effective_from"])
            )
            start = next((day(x) for x in order if x), None)
            if start is None:
                raise ValueError(f"{v['doc_id']} has no date to start its validity")
            starts.append(start)
        for i, v in enumerate(chain):
            end = starts[i + 1] if i + 1 < len(chain) else None
            if end is not None and end <= starts[i]:
                raise ValueError(f"direction {did}: version {i + 2} does not start after {i + 1}")
            windows[v["doc_id"]] = (starts[i], end)
    return windows


@dataclass
class ChunkRun:
    path: Path
    from_cache: bool
    documents: int = 0
    paragraphs: int = 0
    chunks: int = 0
    split_paragraphs: int = 0
    scope_from_listing: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        source = "from cache" if self.from_cache else "written"
        return (
            f"chunk c{CHUNKER_VERSION}: {self.documents} documents, {self.paragraphs} paragraphs,"
            f" {self.chunks} chunks ({self.split_paragraphs} paragraphs split), {source}:"
            f" {self.path}"
        )


def chunk_all(data_dir: Path) -> ChunkRun:
    out_dir = chunks_dir(data_dir)
    out, summary_path = out_dir / "chunks.jsonl", out_dir / "summary.json"
    if out.exists() and summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return ChunkRun(out, True, **summary)

    derived = data_dir / "derived"
    documents = {
        d.doc_id: d
        for d in map(Document.model_validate, _jsonl(derived / "parsed/documents.jsonl"))
    }
    paragraphs: dict[str, list[Paragraph]] = defaultdict(list)
    for raw in _jsonl(derived / "parsed/paragraphs.jsonl"):
        p = Paragraph.model_validate(raw)
        paragraphs[p.doc_id].append(p)
    applies: dict[str, list[str]] = defaultdict(list)
    listed: dict[str, str] = {}
    for f in _jsonl(derived / "parsed/facets.jsonl"):
        if f["key"] == "applies_to":
            applies[f["ref_id"]].append(f["value"])
        elif f["key"] == "listed_under":
            listed[f["ref_id"]] = f["value"]
    labels = {e["code"]: e["label"] for e in _jsonl(derived / "entity_types.jsonl")}
    report = {r["doc_id"]: r for r in _jsonl(derived / "parsed/parse_report.jsonl")}
    windows = validity_windows(_jsonl(derived / "versions.jsonl"), report)

    run = ChunkRun(out, False)
    drafts: list[ChunkDraft] = []
    for doc_id in sorted(paragraphs):
        codes = applies.get(doc_id) or ([listed[doc_id]] if doc_id in listed else [])
        if not applies.get(doc_id):
            run.scope_from_listing.append(doc_id)
        if not codes:
            raise ValueError(f"{doc_id} has no entity scope and no listing category")
        valid_from, valid_to = windows[doc_id]
        rows = chunk_paragraphs(
            documents[doc_id],
            paragraphs[doc_id],
            entity_codes=codes,
            entity_labels=[labels[c] for c in codes],
            valid_from=valid_from,
            valid_to=valid_to,
        )
        drafts.extend(rows)
        run.documents += 1
        run.paragraphs += len(paragraphs[doc_id])
        pieces = Counter(d.para_id for d in rows)
        run.split_paragraphs += sum(1 for n in pieces.values() if n > 1)
    drafts.sort(key=lambda d: (d.doc_id, d.ordinal, d.sub_index))
    run.chunks = len(drafts)

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(dumps(d) + "\n" for d in drafts), encoding="utf-8")
    tmp.replace(out)
    summary = {
        "documents": run.documents,
        "paragraphs": run.paragraphs,
        "chunks": run.chunks,
        "split_paragraphs": run.split_paragraphs,
        "scope_from_listing": run.scope_from_listing,
    }
    summary_path.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return run


def dumps(draft: ChunkDraft) -> str:
    """The canonical serialisation: byte-identical for identical rows."""
    return json.dumps(draft.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_chunks(data_dir: Path) -> list[ChunkDraft]:
    """The chunk rows for the current inputs; raises when ``chunk`` has not run on them."""
    path = chunks_dir(data_dir) / "chunks.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run chunk first")
    return [ChunkDraft.from_json(r) for r in _jsonl(path)]
