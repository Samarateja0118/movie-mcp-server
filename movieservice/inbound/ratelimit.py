"""Inbound sliding-window rate limiting.

Protects the public demo (and the TMDB quota behind it) from a single noisy
client. In-process by design: this is a per-instance guard, and the docstring
says so plainly rather than pretending it is a distributed limiter.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any


class SlidingWindowRateLimiter:
    def __init__(self, *, max_requests: int = 30, window_seconds: int = 3600) -> None:
        self.max_requests = max(1, max_requests)
        self.window_seconds = max(1, window_seconds)
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` and record the hit if allowed."""
        current = time.time() if now is None else now
        window_start = current - self.window_seconds
        hits = self._hits[key]

        while hits and hits[0] <= window_start:
            hits.popleft()

        if len(hits) >= self.max_requests:
            retry_after = max(1, int(self.window_seconds - (current - hits[0])))
            return False, retry_after

        hits.append(current)
        return True, 0

    def clear(self) -> None:
        self._hits.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "tracked_clients": len(self._hits),
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
        }
