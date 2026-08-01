"""Transport layer: retry schedule, circuit breaker, cache, client behaviour."""

from __future__ import annotations

import unittest

import httpx

from movieservice.errors import (
    CircuitOpenError,
    NotFoundError,
    UpstreamRateLimitedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from movieservice.transport import (
    BreakerState,
    CircuitBreaker,
    ResilientHttpClient,
    RetryPolicy,
    TTLCache,
)
from movieservice.transport.retry import parse_retry_after


class RetryPolicyTests(unittest.TestCase):
    def test_stops_after_configured_attempts(self):
        policy = RetryPolicy(attempts=3)

        self.assertTrue(policy.should_retry(1, status=503))
        self.assertTrue(policy.should_retry(2, status=503))
        self.assertFalse(policy.should_retry(3, status=503))

    def test_does_not_retry_client_errors(self):
        policy = RetryPolicy(attempts=3)

        self.assertFalse(policy.should_retry(1, status=400))
        self.assertFalse(policy.should_retry(1, status=401))
        self.assertTrue(policy.should_retry(1, status=429))

    def test_transport_failures_are_always_retryable(self):
        self.assertTrue(RetryPolicy(attempts=2).should_retry(1, status=None))

    def test_backoff_grows_exponentially_and_caps(self):
        policy = RetryPolicy(base_delay=0.2, max_delay=1.0, jitter=0.0)

        delays = [policy.compute_delay(attempt) for attempt in range(1, 5)]

        self.assertEqual(delays, [0.2, 0.4, 0.8, 1.0])

    def test_jitter_stays_within_band(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=10.0, jitter=0.25)

        low = policy.compute_delay(1, rand=0.0)
        high = policy.compute_delay(1, rand=1.0)

        self.assertAlmostEqual(low, 0.75)
        self.assertAlmostEqual(high, 1.25)

    def test_retry_after_header_wins_over_backoff(self):
        policy = RetryPolicy(base_delay=0.2, max_delay=5.0, jitter=0.0)

        self.assertEqual(policy.compute_delay(1, retry_after=3), 3.0)
        # ...but never beyond our own ceiling.
        self.assertEqual(policy.compute_delay(1, retry_after=99), 5.0)

    def test_parse_retry_after_ignores_http_dates(self):
        self.assertEqual(parse_retry_after("12"), 12.0)
        self.assertIsNone(parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT"))
        self.assertIsNone(parse_retry_after(None))


class CircuitBreakerTests(unittest.TestCase):
    def test_opens_after_threshold_failures(self):
        breaker = CircuitBreaker(failure_threshold=3, reset_seconds=10)

        for _ in range(2):
            breaker.record_failure(now=0)
        self.assertIs(breaker.state, BreakerState.CLOSED)

        breaker.record_failure(now=0)
        self.assertIs(breaker.state, BreakerState.OPEN)
        self.assertFalse(breaker.allows(now=1))

    def test_success_resets_the_failure_run(self):
        breaker = CircuitBreaker(failure_threshold=3)

        breaker.record_failure(now=0)
        breaker.record_failure(now=0)
        breaker.record_success()
        breaker.record_failure(now=0)

        self.assertIs(breaker.state, BreakerState.CLOSED)

    def test_half_opens_after_reset_window(self):
        breaker = CircuitBreaker(failure_threshold=1, reset_seconds=10)
        breaker.record_failure(now=0)

        self.assertFalse(breaker.allows(now=5))
        self.assertTrue(breaker.allows(now=10))
        self.assertIs(breaker.state, BreakerState.HALF_OPEN)

    def test_failed_probe_reopens_immediately(self):
        breaker = CircuitBreaker(failure_threshold=5, reset_seconds=10)
        breaker.record_failure(now=0)
        breaker._state = BreakerState.HALF_OPEN  # simulate the probe window

        breaker.record_failure(now=20)

        self.assertIs(breaker.state, BreakerState.OPEN)
        self.assertFalse(breaker.allows(now=21))


class TTLCacheTests(unittest.IsolatedAsyncioTestCase):
    def test_expires_entries(self):
        cache = TTLCache(default_ttl=10)
        cache.set("k", "v", now=0)

        self.assertEqual(cache.get("k", now=5), "v")
        self.assertIsNone(cache.get("k", now=11))

    def test_evicts_least_recently_used(self):
        cache = TTLCache(max_entries=2, default_ttl=100)
        cache.set("a", 1, now=0)
        cache.set("b", 2, now=0)
        cache.get("a", now=0)  # 'a' is now the most recently used
        cache.set("c", 3, now=0)

        self.assertIsNone(cache.get("b", now=0))
        self.assertEqual(cache.get("a", now=0), 1)
        self.assertEqual(cache.get("c", now=0), 3)

    async def test_concurrent_misses_are_coalesced(self):
        import asyncio

        cache = TTLCache(default_ttl=60)
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return "payload"

        results = await asyncio.gather(*(cache.get_or_load("k", loader) for _ in range(5)))

        self.assertEqual(results, ["payload"] * 5)
        self.assertEqual(calls, 1, "single-flight should collapse concurrent misses")

    async def test_loader_failure_is_not_cached(self):
        cache = TTLCache(default_ttl=60)

        async def failing():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await cache.get_or_load("k", failing)

        self.assertIsNone(cache.get("k"))


def build_client(handler, **kwargs) -> ResilientHttpClient:
    """A client wired to an in-memory transport, with sleeps stubbed out."""
    slept: list[float] = []

    async def no_sleep(delay: float) -> None:
        slept.append(delay)

    client = ResilientHttpClient(
        "https://api.example.test",
        headers_provider=lambda: {"Authorization": "Bearer test"},
        sleep=no_sleep,
        name="tmdb",
        **kwargs,
    )
    client._client = httpx.AsyncClient(
        base_url="https://api.example.test",
        transport=httpx.MockTransport(handler),
    )
    client.slept = slept  # type: ignore[attr-defined]
    return client


class ResilientHttpClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_transient_failure_then_succeeds(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"results": []})

        client = build_client(handler, retry=RetryPolicy(attempts=3, base_delay=0.1, jitter=0))
        payload = await client.get_json("/search/movie")

        self.assertEqual(payload, {"results": []})
        self.assertEqual(attempts, 3)
        self.assertEqual(client.slept, [0.1, 0.2])  # exponential backoff between tries
        await client.aclose()

    async def test_retries_timeouts(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("too slow", request=request)
            return httpx.Response(200, json={"ok": True})

        client = build_client(handler, retry=RetryPolicy(attempts=3, jitter=0))

        self.assertEqual(await client.get_json("/movie/1"), {"ok": True})
        self.assertEqual(attempts, 2)
        await client.aclose()

    async def test_raises_timeout_error_when_retries_are_exhausted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("nope", request=request)

        client = build_client(handler, retry=RetryPolicy(attempts=2, jitter=0))

        with self.assertRaises(UpstreamTimeoutError):
            await client.get_json("/movie/1")
        await client.aclose()

    async def test_does_not_retry_unauthorized(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(401, json={"status_message": "invalid token"})

        client = build_client(handler, retry=RetryPolicy(attempts=3, jitter=0))

        with self.assertRaises(UpstreamUnavailableError):
            await client.get_json("/movie/1")
        self.assertEqual(attempts, 1, "a bad token will not fix itself on retry")
        await client.aclose()

    async def test_honours_retry_after_on_429(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})

        client = build_client(handler, retry=RetryPolicy(attempts=2, max_delay=5, jitter=0))

        with self.assertRaises(UpstreamRateLimitedError) as ctx:
            await client.get_json("/movie/1")

        self.assertEqual(client.slept, [2.0])
        self.assertEqual(ctx.exception.retry_after, 2)
        await client.aclose()

    async def test_404_maps_to_not_found_without_retrying(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(404, json={"status_message": "not found"})

        client = build_client(handler, retry=RetryPolicy(attempts=3, jitter=0))

        with self.assertRaises(NotFoundError):
            await client.get_json("/movie/999999")
        self.assertEqual(attempts, 1)
        self.assertIs(client.breaker.state, BreakerState.CLOSED)
        await client.aclose()

    async def test_breaker_opens_and_then_fails_fast(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(500)

        client = build_client(
            handler,
            retry=RetryPolicy(attempts=1, jitter=0),
            breaker=CircuitBreaker(failure_threshold=2, reset_seconds=60, name="tmdb"),
        )

        for _ in range(2):
            with self.assertRaises(UpstreamUnavailableError):
                await client.get_json("/movie/1")

        calls_before = attempts
        with self.assertRaises(CircuitOpenError):
            await client.get_json("/movie/1")

        self.assertEqual(attempts, calls_before, "open circuit must not hit the network")
        await client.aclose()

    async def test_cache_serves_repeat_reads(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, json={"results": [{"id": 1}]})

        client = build_client(handler)

        first = await client.get_json("/search/movie", {"query": "dune"}, cache_ttl=60)
        second = await client.get_json("/search/movie", {"query": "dune"}, cache_ttl=60)

        self.assertEqual(first, second)
        self.assertEqual(attempts, 1)
        self.assertEqual(client.cache.stats()["hits"], 1)
        await client.aclose()

    async def test_cache_key_is_order_independent(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        client = build_client(handler)

        await client.get_json("/search/movie", {"query": "dune", "page": 1}, cache_ttl=60)
        await client.get_json("/search/movie", {"page": 1, "query": "dune"}, cache_ttl=60)

        self.assertEqual(len(seen), 1)
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
