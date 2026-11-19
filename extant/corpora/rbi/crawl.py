"""Step 1, the crawl: listings, the withdrawal list, direction pages and PDFs, into the cache.

Everything after this step works offline from the cache. A second run with nothing new is a
no-op that makes no network request; ``--force`` refetches conditionally. A host that served
a bot challenge is recorded in ``blocked_hosts.json`` and is not contacted again until
``--retry-blocked`` is passed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from extant.corpora.rbi import sources
from extant.corpora.rbi.acquisition import (
    CacheEntry,
    ChallengeError,
    CrawlBlockedError,
    Fetcher,
    HostBlockedError,
    RawCache,
)


@dataclass
class StageResult:
    name: str
    wanted: int = 0
    cached: int = 0  # already in the cache before this run
    fetched: int = 0
    failed: list[str] = field(default_factory=list)
    skipped_blocked: list[str] = field(default_factory=list)


@dataclass
class CrawlResult:
    stages: list[StageResult]
    listing_rows: list[sources.ListingRow]
    network_requests: int
    blocked_hosts: dict[str, dict[str, str]]


class BlockedHosts:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.hosts: dict[str, dict[str, str]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    def add(self, host: str, url: str, reason: str) -> None:
        self.hosts[host] = {
            "since": datetime.now(UTC).isoformat(timespec="seconds"),
            "url": url,
            "reason": reason,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.hosts, indent=1, sort_keys=True), encoding="utf-8")

    def clear(self) -> None:
        self.hosts = {}
        self.path.unlink(missing_ok=True)


def _fetch_all(
    fetcher: Fetcher,
    blocked: BlockedHosts,
    name: str,
    urls: Iterable[str],
    *,
    force: bool,
    expect: str | None = None,
    log: Callable[[str], None],
) -> tuple[StageResult, dict[str, CacheEntry]]:
    result = StageResult(name)
    entries: dict[str, CacheEntry] = {}
    for url in dict.fromkeys(urls):
        result.wanted += 1
        was_cached = fetcher.cache.get(url) is not None
        # A cached URL is a cache hit whatever its host, so PDFs imported by hand count as
        # cached. Only a URL that would need the network is skipped for a blocked host.
        if (force or not was_cached) and httpx.URL(url).host in blocked.hosts:
            result.skipped_blocked.append(url)
            continue
        try:
            entry = fetcher.get(url, force=force, expect=expect)
        except ChallengeError as exc:
            blocked.add(exc.host, exc.url, "bot challenge (CAPTCHA) served instead of content")
            log(f"  ! {exc}")
            result.skipped_blocked.append(url)
            continue
        except HostBlockedError:
            result.skipped_blocked.append(url)
            continue
        if entry is None:
            result.failed.append(url)
            log(f"  ! failed: {url}")
            continue
        entries[url] = entry
        if was_cached and not force:
            result.cached += 1
        else:
            result.fetched += 1
    log(
        f"{name}: {result.wanted} wanted, {result.cached} cached, {result.fetched} fetched,"
        f" {len(result.failed)} failed, {len(result.skipped_blocked)} skipped (host blocked)"
    )
    return result, entries


def crawl(
    fetcher: Fetcher,
    data_dir: Path,
    *,
    force: bool = False,
    retry_blocked: bool = False,
    log: Callable[[str], None] = print,
) -> CrawlResult:
    cache: RawCache = fetcher.cache
    blocked = BlockedHosts(data_dir / "blocked_hosts.json")
    if retry_blocked:
        blocked.clear()
    stages: list[StageResult] = []

    def run(name: str, urls: Iterable[str], expect: str | None = None) -> dict[str, CacheEntry]:
        result, entries = _fetch_all(
            fetcher, blocked, name, urls, force=force, expect=expect, log=log
        )
        stages.append(result)
        return entries

    listing_urls = {
        did: sources.listing_url(did)
        for did in sources.REGULATION_LISTING_IDS + sources.SUPERVISION_LISTING_IDS
    }
    seeds = [
        sources.MASTER_DIRECTIONS,
        *listing_urls.values(),
        sources.WITHDRAWN_CIRCULARS,
        sources.WITHDRAWAL_NOTIFICATION,
        sources.SUPERVISION_WITHDRAWAL_NOTIFICATION,
        sources.CONSOLIDATION_PRESS_RELEASE,
        sources.DRAFT_PRESS_RELEASE,
        sources.STANDALONE_CIRCULARS,
        sources.listing_url(sources.REGULATION_ALL_LISTING_ID),
    ]
    pages = run("listings and notices", seeds, expect="text/html")

    rows: list[sources.ListingRow] = []
    for did, url in listing_urls.items():
        if url in pages:
            department = "regulation" if did in sources.REGULATION_LISTING_IDS else "supervision"
            rows.extend(
                sources.parse_listing(
                    cache.read_bytes(pages[url]), department=department, listing_id=did
                )
            )

    # The corpus (decided 19 November 2026): every listed direction of the Department of
    # Regulation and of the Department of Supervision, plus every direction the withdrawal list
    # links to (directions issued and later withdrawn).
    direction_ids = [r.direction_id for r in rows]
    if sources.WITHDRAWN_CIRCULARS in pages:
        direction_ids += sources.direction_ids_linked_from(
            cache.read_bytes(pages[sources.WITHDRAWN_CIRCULARS])
        )
    direction_ids = list(dict.fromkeys(direction_ids))

    run("direction pages", (sources.detail_url(i) for i in direction_ids), expect="text/html")
    histories = run(
        "version histories", (sources.versions_url(i) for i in direction_ids), expect="text/html"
    )
    earlier: list[str] = []
    for i in direction_ids:
        page = histories.get(sources.versions_url(i))
        if page is not None:
            earlier += [v.url for v in sources.parse_version_history(cache.read_bytes(page), i)]
    run("earlier versions", earlier, expect="text/html")

    notification = pages.get(sources.WITHDRAWAL_NOTIFICATION)
    targets = sources.pdf_targets(
        rows, cache.read_bytes(notification) if notification is not None else None
    )
    run("pdfs", (t.url for t in targets), expect="application/pdf")

    derived = data_dir / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    for name, records in (("listing_rows", rows), ("pdf_targets", targets)):
        with (derived / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(asdict(record), default=str, sort_keys=True) + "\n")

    return CrawlResult(stages, rows, fetcher.network_requests, blocked.hosts)


def regulation_rows_from_cache(cache: RawCache) -> list[sources.ListingRow]:
    """The Department of Regulation listing rows, rebuilt offline from cached pages. Raises if
    the listings have not been crawled yet."""
    rows: list[sources.ListingRow] = []
    for did in sources.REGULATION_LISTING_IDS:
        entry = cache.get(sources.listing_url(did))
        if entry is None:
            raise FileNotFoundError(f"listing {did} is not cached; run crawl first")
        rows.extend(
            sources.parse_listing(cache.read_bytes(entry), department="regulation", listing_id=did)
        )
    return rows


def supervision_rows_from_cache(cache: RawCache) -> list[sources.ListingRow]:
    """The Department of Supervision listing rows, rebuilt offline from cached pages."""
    rows: list[sources.ListingRow] = []
    for did in sources.SUPERVISION_LISTING_IDS:
        entry = cache.get(sources.listing_url(did))
        if entry is None:
            raise FileNotFoundError(f"listing {did} is not cached; run crawl first")
        rows.extend(
            sources.parse_listing(cache.read_bytes(entry), department="supervision", listing_id=did)
        )
    return rows


def listed_rows_from_cache(cache: RawCache) -> list[sources.ListingRow]:
    """Every listed direction of the corpus: both departments (decided 19 November 2026)."""
    return regulation_rows_from_cache(cache) + supervision_rows_from_cache(cache)


def pdf_targets_from_cache(cache: RawCache) -> list[sources.PdfTarget]:
    """The PDF targets, rebuilt offline from cached pages."""
    rows = listed_rows_from_cache(cache)
    notification = cache.get(sources.WITHDRAWAL_NOTIFICATION)
    return sources.pdf_targets(
        rows, cache.read_bytes(notification) if notification is not None else None
    )


__all__ = ["CrawlBlockedError", "CrawlResult", "StageResult", "crawl"]
