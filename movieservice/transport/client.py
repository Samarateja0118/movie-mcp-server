"""The outbound HTTP boundary: one pooled async client, wrapped in policy.

Everything that makes an upstream call resilient lives here — connection
reuse, bounded timeouts, retries with jittered backoff, circuit breaking, and
response caching — so the gateway above it can be written as plain request
mapping, and adapters above *that* never see an ``httpx`` type at all.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import httpx

from ..errors import (
    CircuitOpenError,
    NotFoundError,
    UpstreamError,
    UpstreamRateLimitedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from .breaker import CircuitBreaker
from .cache import TTLCache
from .retry import RetryPolicy, parse_retry_after

logger = logging.getLogger("movieservice.transport")

# Transport-level failures worth another attempt: the request never got a
# meaningful answer, so replaying an idempotent GET is safe.
_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                         httpx.RemoteProtocolError, httpx.PoolTimeout)


class ResilientHttpClient:
    """An async JSON client for one upstream dependency."""

    def __init__(
        self,
        base_url: str,
        *,
        headers_provider: Callable[[], dict[str, str]],
        timeout: float = 8.0,
        connect_timeout: float = 3.0,
        max_connections: int = 20,
        max_keepalive: int = 10,
        retry: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        cache: TTLCache | None = None,
        name: str = "upstream",
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = name
        self._headers_provider = headers_provider
        self._timeout = httpx.Timeout(timeout, connect=connect_timeout)
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
        )
        self.retry = retry or RetryPolicy()
        self.breaker = breaker or CircuitBreaker(name=name)
        self.cache = cache or TTLCache()
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._metrics = {"requests": 0, "retries": 0, "failures": 0}

    # -- lifecycle ---------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Create the pooled client lazily, inside the running event loop."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        base_url=self.base_url,
                        timeout=self._timeout,
                        limits=self._limits,
                    )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "ResilientHttpClient":
        await self._get_client()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # -- requests ----------------------------------------------------------

    async def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        cache_ttl: float | None = None,
    ) -> dict[str, Any]:
        """GET a JSON document, served from cache when one is warm."""
        if cache_ttl is None or cache_ttl <= 0:
            return await self._get_json_uncached(path, params)

        key = self._cache_key(path, params)
        return await self.cache.get_or_load(
            key, lambda: self._get_json_uncached(path, params), ttl=cache_ttl
        )

    async def _get_json_uncached(
        self, path: str, params: dict[str, Any] | None
    ) -> dict[str, Any]:
        if not self.breaker.allows():
            raise CircuitOpenError(
                f"{self.name} is in a failing state; not sending request",
                context={"path": path, "retry_after": self.breaker.retry_after()},
            )

        client = await self._get_client()
        last_error: UpstreamError | None = None

        for attempt in range(1, self.retry.attempts + 1):
            self._metrics["requests"] += 1
            started = time.monotonic()
            try:
                response = await client.get(
                    path, params=params, headers=self._headers_provider()
                )
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = self._transport_error(exc, path)
                if self.retry.should_retry(attempt):
                    await self._backoff(attempt, None, path, repr(exc))
                    continue
                break
            except httpx.HTTPError as exc:  # non-retryable client-side failure
                self.breaker.record_failure()
                self._metrics["failures"] += 1
                raise UpstreamUnavailableError(
                    f"{self.name} request failed: {exc}", context={"path": path}
                ) from exc

            if response.status_code < 400:
                self.breaker.record_success()
                logger.debug(
                    "upstream_ok",
                    extra={
                        "upstream": self.name,
                        "path": path,
                        "attempt": attempt,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return self._decode(response, path)

            if response.status_code == 404:
                # A definitive answer, not a fault: don't trip the breaker.
                self.breaker.record_success()
                raise NotFoundError(
                    f"{self.name} has no record for {path}", context={"path": path}
                )

            last_error = self._status_error(response, path)
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            if self.retry.should_retry(attempt, status=response.status_code):
                await self._backoff(attempt, retry_after, path, f"HTTP {response.status_code}")
                continue
            break

        self.breaker.record_failure()
        self._metrics["failures"] += 1
        raise last_error or UpstreamUnavailableError(
            f"{self.name} request failed", context={"path": path}
        )

    # -- helpers -----------------------------------------------------------

    async def _backoff(
        self, attempt: int, retry_after: float | None, path: str, reason: str
    ) -> None:
        delay = self.retry.compute_delay(attempt, retry_after=retry_after)
        self._metrics["retries"] += 1
        logger.warning(
            "upstream_retry",
            extra={
                "upstream": self.name,
                "path": path,
                "attempt": attempt,
                "reason": reason,
                "delay_s": round(delay, 3),
            },
        )
        await self._sleep(delay)

    def _decode(self, response: httpx.Response, path: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"{self.name} returned a non-JSON body", context={"path": path}
            ) from exc
        if not isinstance(payload, dict):
            raise UpstreamError(
                f"{self.name} returned an unexpected JSON shape", context={"path": path}
            )
        return payload

    def _transport_error(self, exc: Exception, path: str) -> UpstreamError:
        if isinstance(exc, httpx.TimeoutException):
            return UpstreamTimeoutError(
                f"{self.name} timed out", context={"path": path}
            )
        return UpstreamUnavailableError(
            f"{self.name} is unreachable: {exc}", context={"path": path}
        )

    def _status_error(self, response: httpx.Response, path: str) -> UpstreamError:
        status = response.status_code
        context = {"path": path, "status": status}
        if status == 429:
            retry_after = parse_retry_after(response.headers.get("retry-after")) or 1
            return UpstreamRateLimitedError(
                f"{self.name} rate limited this service",
                retry_after=int(retry_after),
                context=context,
            )
        if status in (401, 403):
            return UpstreamUnavailableError(
                f"{self.name} rejected our credentials (HTTP {status})", context=context
            )
        return UpstreamUnavailableError(
            f"{self.name} returned HTTP {status}", context=context
        )

    @staticmethod
    def _cache_key(path: str, params: dict[str, Any] | None) -> str:
        if not params:
            return path
        encoded = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{path}?{encoded}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "upstream": self.name,
            "metrics": dict(self._metrics),
            "breaker": self.breaker.snapshot(),
            "cache": self.cache.stats(),
        }
