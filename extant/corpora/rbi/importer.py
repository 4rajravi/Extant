"""Manual PDF import: files PDFs a person downloaded in a browser into the raw cache.

Why this exists. ``rbidocs.rbi.org.in`` serves a CAPTCHA to automated clients, and Extant
never answers or works around a CAPTCHA. A person can download the PDFs in their own browser.
This module files those bytes under the URL they came from, so extraction and everything after
it treat them exactly like fetched bytes, while the cache entry (``origin="imported"``) and the
manifest (``outcome="imported"``) record that a person downloaded them, not the crawler.

Matching is by file name. Every target URL has a unique file name, checked here, and browsers
save a download under that name, adding a counter such as `` (1)`` for a repeat. Nothing here
parses a PDF: the only check on content is the ``%PDF-`` header, the same guard extraction
applies, so a saved CAPTCHA page cannot be imported as a direction.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from extant.core.hashing import sha256_hex_bytes
from extant.corpora.rbi.acquisition import CacheEntry, ManifestRow, RawCache

# A browser's counter for a repeated download: "204MD (1).PDF", "204MD(1).PDF", "204MD-1.PDF".
_COUNTER = re.compile(r"^(?P<stem>.+?)(?:\s?\(\d+\)|-\d+)(?P<ext>\.pdf)$", re.IGNORECASE)


def file_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].lower()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class ImportReport:
    imported: list[tuple[str, str]] = field(default_factory=list)  # (file, url)
    already: list[tuple[str, str]] = field(default_factory=list)  # same bytes already cached
    conflicts: list[tuple[str, str]] = field(default_factory=list)  # other bytes already cached
    not_pdf: list[str] = field(default_factory=list)  # no %PDF- header
    unmatched: list[str] = field(default_factory=list)  # names no target has
    missing: list[str] = field(default_factory=list)  # target URLs still not cached

    def __str__(self) -> str:
        return (
            f"imported {len(self.imported)}, already cached {len(self.already)},"
            f" conflicts {len(self.conflicts)}, not a PDF {len(self.not_pdf)},"
            f" unmatched {len(self.unmatched)}, still missing {len(self.missing)}"
        )


def _match(name: str, by_name: dict[str, str]) -> str | None:
    lowered = name.lower()
    if lowered in by_name:
        return by_name[lowered]
    m = _COUNTER.fullmatch(lowered)
    if m is not None:
        return by_name.get(m["stem"] + m["ext"])
    return None


def import_pdfs(
    cache: RawCache,
    folder: Path,
    target_urls: Sequence[str],
    *,
    replace: bool = False,
    now: Callable[[], str] = _now,
) -> ImportReport:
    """Import every ``*.pdf`` in ``folder`` whose name matches a target URL.

    Bytes already cached for that URL are left alone and reported; different bytes are a
    conflict unless ``replace`` is set, so re-running an import is always safe.
    """
    by_name: dict[str, str] = {}
    for target in target_urls:
        name = file_name(target)
        if name in by_name and by_name[name] != target:
            raise ValueError(f"two targets share the file name {name!r}; cannot match by name")
        by_name[name] = target
    if not folder.is_dir():
        raise FileNotFoundError(f"not a folder: {folder}")

    report = ImportReport()
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    for path in files:
        url = _match(path.name, by_name)
        if url is None:
            report.unmatched.append(path.name)
            continue
        data = path.read_bytes()
        if b"%PDF-" not in data[:1024]:
            report.not_pdf.append(path.name)
            continue
        sha = sha256_hex_bytes(data)
        existing = cache.get(url)
        if existing is not None and existing.source_sha256 == sha:
            report.already.append((path.name, url))
            continue
        if existing is not None and not replace:
            report.conflicts.append((path.name, url))
            continue
        stamp = now()
        cache.put(
            CacheEntry(
                url=url,
                final_url=url,
                status=None,
                fetched_at=stamp,
                source_sha256=sha,
                bytes=len(data),
                content_type="application/pdf",
                etag=None,
                last_modified=None,
                origin="imported",
                imported_file=path.name,
            ),
            data,
        )
        cache.append_manifest(
            ManifestRow(
                url,
                stamp,
                "imported",
                None,
                None,
                len(data),
                sha,
                0,
                url,
                "application/pdf",
                error=f"downloaded by a person and imported from {path.name}",
            )
        )
        report.imported.append((path.name, url))

    report.missing = [t for t in target_urls if cache.get(t) is None]
    return report


def write_download_page(targets: Sequence[tuple[str, str]], path: Path) -> None:
    """A plain HTML page of links, ``(url, label)`` each, for a person to download from."""
    rows = "\n".join(
        f'<tr><td>{i}</td><td><a href="{html.escape(url)}">{html.escape(file_name(url))}</a></td>'
        f"<td>{html.escape(label)}</td></tr>"
        for i, (url, label) in enumerate(targets, 1)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>RBI PDFs to download</title>\n"
        "<style>body{font:14px system-ui;margin:24px}td{padding:2px 10px}"
        "tr:nth-child(even){background:#f4f4f4}</style>\n"
        f"<h1>{len(targets)} RBI PDFs still to download</h1>\n"
        "<p>Save every file into one folder, keeping the file names, then run "
        "<code>uv run python -m extant.corpora.rbi import-pdfs &lt;folder&gt;</code>. "
        "Set the browser to download PDFs rather than open them, and answer any CAPTCHA "
        "yourself. Re-running the import is safe.</p>\n"
        f"<table>\n{rows}\n</table>\n",
        encoding="utf-8",
    )
