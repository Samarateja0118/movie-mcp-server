"""TTL response cache with single-flight de-duplication.

Two things this buys us:

* **Latency.** Repeat prompts (the demo's suggestion chips, an agent re-reading
  a movie it just looked up) skip the network entirely.
* **Load shedding.** Without single-flight, ten concurrent requests for a cold
  key become ten upstream calls. Here they share one in-flight coroutine.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


class TTLCache:
    def __init__(self, *, max_entries: int = 512, default_ttl: float = 60.0) -> None:
        self.max_entries = max(1, max_entries)
        self.default_ttl = default_ttl
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self._hits = 0
        self._misses = 0
        self._coalesced = 0

    def get(self, key: str, *, now: float | None = None) -> Any | None:
        current = time.monotonic() if now is None else now
        entry = self._entries.get(key)
        if entry is None:
            return None

        expires_at, value = entry
        if expires_at <= current:
            del self._entries[key]
            return None

        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: Any, *, ttl: float | None = None, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        effective_ttl = self.default_ttl if ttl is None else ttl
        if effective_ttl <= 0:
            return

        self._entries[key] = (current + effective_ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)  # evict least recently used

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        *,
        ttl: float | None = None,
    ) -> T:
        cached = self.get(key)
        if cached is not None:
            self._hits += 1
            return cached

        inflight = self._inflight.get(key)
        if inflight is not None:
            self._coalesced += 1
            return await asyncio.shield(inflight)

        self._misses += 1
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await loader()
        except BaseException as exc:  # propagate to every coalesced waiter
            if not future.done():
                future.set_exception(exc)
            raise
        else:
            self.set(key, value, ttl=ttl)
            if not future.done():
                future.set_result(value)
            return value
        finally:
            self._inflight.pop(key, None)
            # Keep an unconsumed exception from being reported as "never retrieved".
            if future.done() and not future.cancelled() and future.exception() is not None:
                future.exception()

    def clear(self) -> None:
        self._entries.clear()

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "entries": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
            "coalesced": self._coalesced,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
        }
