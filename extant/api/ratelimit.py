"""Per client rate limiting, in memory, and where the client address comes from.

Approximate by design. Each instance counts on its own and forgets on a cold start, so with
several instances a caller can make up to that many times the limit, and a scaled-to-zero
service resets every counter. That is acceptable because this limit is anti-abuse only: the
daily cap, which is the cost control, is exact and lives in Postgres.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from starlette.requests import Request


def client_address(request: Request, trusted_hops: int) -> str:
    """The caller's address. With ``trusted_hops`` 0 the socket's peer, and the
    X-Forwarded-For header is ignored, since anyone can send one. With N trusted proxies,
    each of which appends the address it received from, the caller is the Nth entry from the
    right: entries further left were supplied by the caller and are not evidence."""
    peer = request.client.host if request.client else "unknown"
    if trusted_hops == 0:
        return peer
    header = ",".join(request.headers.getlist("x-forwarded-for"))
    entries = [e.strip() for e in header.split(",") if e.strip()]
    if len(entries) < trusted_hops:
        return peer  # not the shape the configured proxies produce: fall back, never trust
    return entries[-trusted_hops]


class RateLimiter:
    """A sliding window of ``limit`` requests per ``window`` seconds per key."""

    def __init__(
        self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._sweeps = 0

    def hit(self, key: str) -> int | None:
        """Count one request. None when allowed; otherwise the whole seconds until it would
        be, for Retry-After."""
        now = self._clock()
        with self._lock:
            self._sweep(now)
            seen = self._seen.setdefault(key, deque())
            while seen and seen[0] <= now - self.window:
                seen.popleft()
            if len(seen) >= self.limit:
                return max(1, math.ceil(seen[0] + self.window - now))
            seen.append(now)
            return None

    def _sweep(self, now: float) -> None:
        # Drop keys idle for a whole window, every so often, so memory follows active callers.
        self._sweeps += 1
        if self._sweeps % 1000:
            return
        for key in [k for k, s in self._seen.items() if not s or s[-1] <= now - self.window]:
            del self._seen[key]
