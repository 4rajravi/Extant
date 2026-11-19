"""Step 3 over the whole corpus: parse every version, cache it, write the parsed JSONL.

Each version (``derived/versions.jsonl``'s order of identity) is parsed from its extracted
page. The result is cached under
``derived/parsed/<HTML_EXTRACTOR_VERSION>-p<PARSER_VERSION>/<sha[:2]>/<source_sha256>.json``, so
an unchanged rerun parses nothing and a parser change reparses everything. Outputs, all git
ignored and all rebuilt each run from the cache:

``derived/parsed/paragraphs.jsonl``     every Paragraph record, Phase 0 model dumps
``derived/parsed/documents.jsonl``      one Document record per version that has a title
``derived/parsed/facets.jsonl``         document facets: applies_to, listed_under, dates, sources
``derived/parsed/parse_report.jsonl``   one row per version: status, counts, warnings, accounting
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from extant.core.models import Document, Facet, Paragraph
from extant.corpora.rbi import sources
from extant.corpora.rbi.acquisition import RawCache
from extant.corpora.rbi.crawl import listed_rows_from_cache
from extant.corpora.rbi.entities import category_for_listing
from extant.corpora.rbi.extract_html import (
    HTML_EXTRACTOR_VERSION,
    html_extract_path,
    load_html_document,
)
from extant.corpora.rbi.mine import identities_from_cache, versions_from_cache
from extant.corpora.rbi.parse import PARSER_VERSION, ParsedDocument, Warning, parse_blocks


def parse_cache_dir(data_dir: Path) -> Path:
    return data_dir / "derived" / "parsed" / f"{HTML_EXTRACTOR_VERSION}-p{PARSER_VERSION}"


def _dump(p: ParsedDocument) -> dict[str, Any]:
    raw = asdict(p)
    raw["document"] = p.document.model_dump(mode="json") if p.document else None
    raw["paragraphs"] = [x.model_dump(mode="json") for x in p.paragraphs]
    return raw


def _load(raw: dict[str, Any]) -> ParsedDocument:
    raw = dict(raw)
    raw["document"] = Document.model_validate(raw["document"]) if raw["document"] else None
    raw["paragraphs"] = [Paragraph.model_validate(x) for x in raw["paragraphs"]]
    raw["warnings"] = [Warning(**w) for w in raw["warnings"]]
    for key in ("issued_on", "effective_from", "updated_as_on"):
        raw[key] = date.fromisoformat(raw[key]) if raw[key] else None
    return ParsedDocument(**raw)


@dataclass
class ParseRun:
    versions: int = 0
    parsed_now: int = 0
    from_cache: int = 0
    status: Counter[str] = field(default_factory=Counter)
    paragraphs: int = 0
    results: list[tuple[dict[str, Any], ParsedDocument]] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"parse p{PARSER_VERSION}: {self.versions} versions, {self.parsed_now} parsed now,"
            f" {self.from_cache} from cache, {self.paragraphs} paragraphs,"
            f" status {dict(sorted(self.status.items()))}"
        )


def _facets(version: dict[str, Any], p: ParsedDocument, listed_code: str | None) -> list[Facet]:
    doc_id = p.doc_id
    out = [
        Facet(scope="document", ref_id=doc_id, key="applies_to", value=c) for c in p.applicability
    ]
    pairs = [
        ("applicability_status", p.applicability_status),
        ("effective_basis", p.effective_basis),
        ("parse_status", p.status),
        ("listed_under", listed_code),
        ("title_source", p.title_source),
        ("updated_as_on", p.updated_as_on.isoformat() if p.updated_as_on else None),
        ("rbi_page_id", str(version["direction_id"])),
    ]
    out += [
        Facet(scope="document", ref_id=doc_id, key=k, value=v) for k, v in pairs if v is not None
    ]
    return out


def parse_all(data_dir: Path, *, sample: int | None = None) -> ParseRun:
    cache = RawCache(data_dir)
    identities = identities_from_cache(data_dir)
    versions, _ = versions_from_cache(data_dir, identities)
    listing = {r.direction_id: r for r in listed_rows_from_cache(cache)}
    ordered = sorted(versions, key=lambda v: v.doc_id)
    if sample is not None:
        ordered = ordered[:sample]
    run = ParseRun()
    out_dir = parse_cache_dir(data_dir)
    for v in ordered:
        run.versions += 1
        entry = cache.get(v.url)
        if entry is None:
            continue
        cached = out_dir / entry.source_sha256[:2] / f"{entry.source_sha256}.json"
        if cached.exists():
            parsed = _load(json.loads(cached.read_text(encoding="utf-8")))
            run.from_cache += 1
        else:
            row = listing.get(v.direction_id)
            parsed = parse_blocks(
                load_html_document(html_extract_path(data_dir, entry.source_sha256)),
                doc_id=v.doc_id,
                fallback_title=row.title if row else None,
                source_url=v.url,
                supersedes=v.supersedes,
            )
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(
                json.dumps(_dump(parsed), ensure_ascii=False, default=str), encoding="utf-8"
            )
            run.parsed_now += 1
        run.status[parsed.status] += 1
        run.paragraphs += len(parsed.paragraphs)
        run.results.append((asdict(v), parsed))
    _write_outputs(data_dir, run, listing)
    return run


def _write_outputs(data_dir: Path, run: ParseRun, listing: dict[int, sources.ListingRow]) -> None:
    out = data_dir / "derived" / "parsed"
    out.mkdir(parents=True, exist_ok=True)
    with (
        (out / "paragraphs.jsonl").open("w", encoding="utf-8") as paras,
        (out / "documents.jsonl").open("w", encoding="utf-8") as docs,
        (out / "facets.jsonl").open("w", encoding="utf-8") as facets,
        (out / "parse_report.jsonl").open("w", encoding="utf-8") as report,
    ):
        for version, p in run.results:
            row = listing.get(version["direction_id"])
            code = category_for_listing(row.listing_id).code if row else None
            for para in p.paragraphs:
                paras.write(json.dumps(para.model_dump(mode="json"), ensure_ascii=False) + "\n")
            if p.document is not None:
                docs.write(
                    json.dumps(p.document.model_dump(mode="json"), ensure_ascii=False) + "\n"
                )
            for f in _facets(version, p, code):
                facets.write(json.dumps(f.model_dump(mode="json"), ensure_ascii=False) + "\n")
            report.write(
                json.dumps(
                    {
                        "doc_id": p.doc_id,
                        "direction_id": version["direction_id"],
                        "version": version["version"],
                        "current": version["current"],
                        "department": row.department if row else None,
                        "listed_under": code,
                        "title": p.title,
                        "status": p.status,
                        "failure": p.failure,
                        "failure_anchor": p.failure_anchor,
                        "paragraphs": len(p.paragraphs),
                        "chapters": len(p.chapters),
                        "annexes": len(p.annexes),
                        "body_chars": sum(len(x.body) for x in p.paragraphs),
                        "applicability": p.applicability,
                        "applicability_status": p.applicability_status,
                        "issued_on": p.issued_on.isoformat() if p.issued_on else None,
                        "effective_from": p.effective_from.isoformat()
                        if p.effective_from
                        else None,
                        "effective_basis": p.effective_basis,
                        "updated_as_on": p.updated_as_on.isoformat() if p.updated_as_on else None,
                        "warnings": dict(Counter(w.code for w in p.warnings)),
                        "first_warning": asdict(p.warnings[0]) if p.warnings else None,
                        "block_accounting": p.block_accounting,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
