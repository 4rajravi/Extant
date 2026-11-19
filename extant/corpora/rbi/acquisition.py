"""Acquisition: raw bytes on disk and the fetch manifest, nothing more.

This module never parses, interprets or cleans what it fetches. Its interface is one cached
file plus one manifest row per fetch, keyed by URL. Link discovery, extraction and parsing
all read from the cache through other modules.

Cache layout under ``data_dir`` (documented for later phases in corpora/rbi/README.md)::

    blobs/<sha[:2]>/<sha>          raw response bytes, content addressed by sha256
    responses/<url_key>.json       the cache entry for a URL: status, headers, sha256
    manifest.jsonl                 append only, one row per fetch, successes and failures

``url_key`` is the sha256 of the URL string. Only 2xx responses become cache entries; a
failure is recorded in the manifest and retried by the next crawl run.

Politeness, which is not configurable downwards: one request at a time, at least
``MIN_REQUEST_INTERVAL_SECONDS`` between any two requests (each redirect hop counts),
exponential backoff on 5xx, 429 and transport errors, three attempts at most, and a hard
stop of the whole crawl as soon as the site starts refusing us.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from extant.core.hashing import sha256_hex_bytes
from extant.corpora.rbi.settings import MIN_REQUEST_INTERVAL_SECONDS

MAX_ATTEMPTS = 3
MAX_REDIRECTS = 5
BACKOFF_BASE_SECONDS = 4.0
# Statuses that mean the site is refusing this client. Seeing them repeatedly stops the
# crawl instead of hammering a firewall.
REFUSAL_STATUSES = frozenset({401, 403, 418, 429})
REFUSALS_BEFORE_STOP = 3
_RETRYABLE = frozenset({429, 500, 502, 503, 504})
_DROPPED_HEADERS = frozenset({"set-cookie"})


class CrawlBlockedError(RuntimeError):
    """The site refused several requests in a row. The crawl stops; it does not push on."""


class ChallengeError(CrawlBlockedError):
    """A host served a bot challenge (a CAPTCHA) in place of the resource. Extant never
    answers or works around one; the host is off limits for the rest of the run."""

    def __init__(self, host: str, url: str) -> None:
        super().__init__(f"{host} served a bot challenge instead of {url}; not fetching from it")
        self.host = host
        self.url = url


class HostBlockedError(CrawlBlockedError):
    """A URL on a host that already served a challenge in this run. No request was made."""

    def __init__(self, host: str, url: str) -> None:
        super().__init__(f"{host} is blocked for this run; skipped {url}")
        self.host = host
        self.url = url


# Byte signatures of the site's bot challenge and refusal pages. Matching them is a check
# that the body is not the resource at all, not an interpretation of its content.
CHALLENGE_SIGNATURES = (
    b"testing whether you are a human visitor",
    b"What code is in the image?",
    b"You are not authorized to view this page",
)


def is_challenge(body: bytes) -> bool:
    head = body[:65536]
    return any(signature in head for signature in CHALLENGE_SIGNATURES)


def url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class CacheEntry:
    url: str
    final_url: str
    status: int | None  # None for an imported file: no HTTP response was observed
    fetched_at: str  # for an imported file, when it was imported
    source_sha256: str
    bytes: int
    content_type: str | None
    etag: str | None
    last_modified: str | None
    headers: dict[str, str] = field(default_factory=dict)
    origin: str = "fetched"  # fetched | imported (downloaded by a person, see importer.py)
    imported_file: str | None = None  # the file name it was imported from


@dataclass(frozen=True, slots=True)
class ManifestRow:
    url: str
    fetched_at: str
    outcome: str  # fetched | not_modified | failed | blocked | invalidated | imported
    status: int | None
    etag: str | None
    bytes: int | None
    source_sha256: str | None
    attempts: int
    final_url: str | None = None
    content_type: str | None = None
    error: str | None = None


# --------------------------------------------------------------------------- cache


class RawCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.blobs = root / "blobs"
        self.responses = root / "responses"
        self.manifest_path = root / "manifest.jsonl"

    def _entry_path(self, url: str) -> Path:
        return self.responses / f"{url_key(url)}.json"

    def blob_path(self, sha256: str) -> Path:
        return self.blobs / sha256[:2] / sha256

    def get(self, url: str) -> CacheEntry | None:
        path = self._entry_path(url)
        if not path.exists():
            return None
        return CacheEntry(**json.loads(path.read_text(encoding="utf-8")))

    def read_bytes(self, entry: CacheEntry) -> bytes:
        data = self.blob_path(entry.source_sha256).read_bytes()
        if sha256_hex_bytes(data) != entry.source_sha256:
            raise OSError(f"cache blob for {entry.url} is corrupt")
        return data

    def put(self, entry: CacheEntry, data: bytes) -> None:
        blob = self.blob_path(entry.source_sha256)
        if not blob.exists():
            _write_atomic(blob, data)
        _write_atomic(
            self._entry_path(entry.url),
            json.dumps(asdict(entry), indent=1, sort_keys=True).encode("utf-8"),
        )

    def invalidate(self, url: str, reason: str) -> bool:
        """Drop the cache entry for ``url`` and say so in the manifest. The blob stays,
        since content addressed bytes may be shared; the manifest stays append only."""
        entry = self.get(url)
        if entry is None:
            return False
        self._entry_path(url).unlink()
        self.append_manifest(
            ManifestRow(
                url,
                _now(),
                "invalidated",
                entry.status,
                entry.etag,
                entry.bytes,
                entry.source_sha256,
                0,
                entry.final_url,
                entry.content_type,
                error=reason,
            )
        )
        return True

    def append_manifest(self, row: ManifestRow) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(row), sort_keys=True) + "\n")

    def manifest(self) -> Iterator[ManifestRow]:
        if not self.manifest_path.exists():
            return
        with self.manifest_path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield ManifestRow(**json.loads(line))

    def entries(self) -> Iterator[CacheEntry]:
        if not self.responses.exists():
            return
        for path in sorted(self.responses.glob("*.json")):
            yield CacheEntry(**json.loads(path.read_text(encoding="utf-8")))


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_bytes(data)
    tmp.replace(path)


# --------------------------------------------------------------------------- politeness


class RateLimiter:
    """Guarantees a minimum gap between the starts of any two requests."""

    def __init__(
        self,
        interval: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval < MIN_REQUEST_INTERVAL_SECONDS:
            raise ValueError(f"request interval below {MIN_REQUEST_INTERVAL_SECONDS}s")
        self.interval = interval
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self, extra: float = 0.0) -> None:
        if self._last is not None:
            remaining = self._last + self.interval + extra - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


# --------------------------------------------------------------------------- fetcher


class Fetcher:
    """HTTP GET through the cache. A URL already cached is never fetched again unless
    ``force`` is passed, and then conditionally on its ETag or Last-Modified."""

    def __init__(
        self,
        cache: RawCache,
        client: httpx.Client,
        limiter: RateLimiter,
        *,
        on_fetch: Callable[[ManifestRow], None] | None = None,
    ) -> None:
        self.cache = cache
        self.client = client
        self.limiter = limiter
        self.on_fetch = on_fetch
        self.network_requests = 0
        self.blocked_hosts: set[str] = set()
        self._consecutive_refusals = 0

    def get(self, url: str, *, force: bool = False, expect: str | None = None) -> CacheEntry | None:
        """Return the cached entry for ``url``, fetching it first if needed. Returns None
        when the fetch failed; the failure is in the manifest. ``expect`` is a content type
        prefix such as ``application/pdf``; a response of another type is not cached.

        Raises ChallengeError when the host serves a bot challenge, and HostBlockedError for
        any later URL on that host, without touching the network."""
        cached = self.cache.get(url)
        if cached is not None and not force:
            return cached
        host = httpx.URL(url).host
        if host in self.blocked_hosts:
            raise HostBlockedError(host, url)

        conditional: dict[str, str] = {}
        if cached is not None:
            if cached.etag:
                conditional["If-None-Match"] = cached.etag
            if cached.last_modified:
                conditional["If-Modified-Since"] = cached.last_modified

        response, attempts, error = self._request(url, conditional)
        fetched_at = _now()

        if response is None:
            self._record(
                ManifestRow(
                    url, fetched_at, "failed", None, None, None, None, attempts, error=error
                )
            )
            return None

        status = response.status_code
        etag = response.headers.get("etag")
        final_url = str(response.url)

        if status == 304 and cached is not None:
            self._record(
                ManifestRow(
                    url,
                    fetched_at,
                    "not_modified",
                    status,
                    etag or cached.etag,
                    cached.bytes,
                    cached.source_sha256,
                    attempts,
                    final_url,
                    cached.content_type,
                )
            )
            return cached

        content_type = response.headers.get("content-type")

        def failed(outcome: str, reason: str) -> None:
            self._record(
                ManifestRow(
                    url,
                    fetched_at,
                    outcome,
                    status,
                    etag,
                    len(response.content),
                    None,
                    attempts,
                    final_url,
                    content_type,
                    error=reason,
                )
            )

        if not 200 <= status < 300:
            failed("failed", f"HTTP {status}")
            return None

        if is_challenge(response.content):
            # The site answered 200 with a bot challenge instead of the resource. Never cache
            # it, never try to answer it, and stop asking this host for anything.
            host = httpx.URL(url).host
            self.blocked_hosts.add(host)
            failed("blocked", "bot challenge page served instead of the resource")
            raise ChallengeError(host, url)

        if expect is not None and not (content_type or "").lower().startswith(expect):
            failed("failed", f"expected {expect}, got {content_type}")
            return None

        data = response.content
        entry = CacheEntry(
            url=url,
            final_url=final_url,
            status=status,
            fetched_at=fetched_at,
            source_sha256=sha256_hex_bytes(data),
            bytes=len(data),
            content_type=response.headers.get("content-type"),
            etag=etag,
            last_modified=response.headers.get("last-modified"),
            headers={
                k.lower(): v
                for k, v in response.headers.items()
                if k.lower() not in _DROPPED_HEADERS
            },
        )
        self.cache.put(entry, data)
        self._record(
            ManifestRow(
                url,
                fetched_at,
                "fetched",
                status,
                etag,
                entry.bytes,
                entry.source_sha256,
                attempts,
                final_url,
                entry.content_type,
            )
        )
        return entry

    def _record(self, row: ManifestRow) -> None:
        self.cache.append_manifest(row)
        if self.on_fetch is not None:
            self.on_fetch(row)

    def _request(
        self, url: str, headers: dict[str, str]
    ) -> tuple[httpx.Response | None, int, str | None]:
        error: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            backoff = 0.0 if attempt == 1 else BACKOFF_BASE_SECONDS * 2 ** (attempt - 2)
            try:
                response = self._follow(url, headers, backoff)
            except httpx.TransportError as exc:
                error = f"{type(exc).__name__}: {exc}"
                continue
            self._note_refusal(response.status_code, url)
            if response.status_code in _RETRYABLE:
                error = f"HTTP {response.status_code}"
                if attempt == MAX_ATTEMPTS:
                    return response, attempt, error
                continue
            return response, attempt, None
        return None, MAX_ATTEMPTS, error

    def _follow(self, url: str, headers: dict[str, str], backoff: float) -> httpx.Response:
        current = httpx.URL(url)
        for _ in range(MAX_REDIRECTS + 1):
            self.limiter.wait(extra=backoff)
            backoff = 0.0
            self.network_requests += 1
            response = self.client.get(current, headers=headers, follow_redirects=False)
            if not response.is_redirect:
                response.read()
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current = current.join(location)
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects from {url}")

    def _note_refusal(self, status: int, url: str) -> None:
        if status in REFUSAL_STATUSES:
            self._consecutive_refusals += 1
            if self._consecutive_refusals >= REFUSALS_BEFORE_STOP:
                raise CrawlBlockedError(
                    f"the site refused {self._consecutive_refusals} requests in a row"
                    f" (last: HTTP {status} for {url}); stopping the crawl"
                )
        else:
            self._consecutive_refusals = 0


def make_client(user_agent: str, timeout: float, **kwargs: Any) -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        },
        timeout=timeout,
        **kwargs,
    )
