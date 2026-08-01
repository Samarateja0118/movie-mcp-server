"""Circuit breaker — the fault-isolation boundary around a single dependency.

When TMDB is down, retrying every inbound request just converts an upstream
outage into slow timeouts for our own callers. The breaker trips after a run of
failures and fails fast until a probe succeeds, so one sick dependency cannot
consume this service's connection pool or latency budget.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any


class BreakerState(str, Enum):
    CLOSED = "closed"  # traffic flows normally
    OPEN = "open"  # failing fast, dependency presumed down
    HALF_OPEN = "half_open"  # letting a single probe through


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_seconds: float = 20.0,
        name: str = "upstream",
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.reset_seconds = reset_seconds
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._trips = 0

    @property
    def state(self) -> BreakerState:
        return self._state

    def allows(self, now: float | None = None) -> bool:
        """Should the next call be attempted?"""
        current = time.monotonic() if now is None else now
        if self._state is BreakerState.OPEN:
            if current - self._opened_at >= self.reset_seconds:
                self._state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = BreakerState.CLOSED

    def record_failure(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        self._consecutive_failures += 1

        # A failed probe in half-open sends us straight back to open.
        if (
            self._state is BreakerState.HALF_OPEN
            or self._consecutive_failures >= self.failure_threshold
        ):
            if self._state is not BreakerState.OPEN:
                self._trips += 1
            self._state = BreakerState.OPEN
            self._opened_at = current

    def retry_after(self, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        remaining = self.reset_seconds - (current - self._opened_at)
        return max(1, int(remaining))

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self._state.value,
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "trips": self._trips,
        }
