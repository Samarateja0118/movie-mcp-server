"""Retry policy: what is worth retrying, and how long to wait.

Kept as a pure, dependency-free object so the backoff schedule can be unit
tested without sleeping or touching the network.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# Status codes where a second attempt has a real chance of succeeding.
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.2
    max_delay: float = 2.0
    jitter: float = 0.25
    retryable_statuses: frozenset[int] = field(default=RETRYABLE_STATUSES)

    def should_retry(self, attempt: int, *, status: int | None = None) -> bool:
        """``attempt`` is 1-based: attempt 1 is the original request."""
        if attempt >= self.attempts:
            return False
        if status is None:  # transport-level failure (timeout, connect error)
            return True
        return status in self.retryable_statuses

    def compute_delay(
        self,
        attempt: int,
        *,
        retry_after: float | None = None,
        rand: float | None = None,
    ) -> float:
        """Exponential backoff with full-range jitter, capped at ``max_delay``.

        A ``Retry-After`` hint from the server always wins over our own guess —
        the upstream knows better than we do when it will be ready.
        """
        if retry_after is not None and retry_after >= 0:
            return min(float(retry_after), self.max_delay)

        backoff = min(self.base_delay * (2 ** max(attempt - 1, 0)), self.max_delay)
        if self.jitter <= 0:
            return backoff

        roll = random.random() if rand is None else rand
        spread = backoff * self.jitter
        return max(0.0, backoff - spread + (roll * 2 * spread))


def parse_retry_after(value: str | None) -> float | None:
    """Parse the delay-seconds form of ``Retry-After``; ignore HTTP-date form."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
