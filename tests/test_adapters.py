"""Adapters: the HTTP API and the MCP tool surface.

Both are exercised against the same fake gateway, which is the point — one
domain service, two transports, identical behaviour.
"""

from __future__ import annotations

import unittest

from starlette.testclient import TestClient

import server
import webapp
from movieservice.catalog import CatalogService
from movieservice.errors import NotFoundError, UpstreamRateLimitedError
from movieservice.models import MovieSummary

from .test_catalog import FakeGateway, summary


class HttpAdapterTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway(
            search_results=[summary(693134, "Dune: Part Two", "2024")],
            discover_results=[summary(1, "Smile", "2022")],
        )
        self._original_service = webapp.service
        webapp.service = CatalogService(self.gateway)
        webapp.rate_limiter.clear()
        self.client = TestClient(webapp.app)

    def tearDown(self):
        webapp.service = self._original_service
        webapp.rate_limiter.clear()

    def test_search_returns_the_documented_contract(self):
        response = self.client.post("/api/search", json={"query": "Dune 2024"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["query"], "Dune 2024")
        self.assertEqual(payload["results"][0]["title"], "Dune: Part Two")
        # Keys the browser client reads by name.
        self.assertEqual(
            set(payload["parsed"]),
            {"genre_id", "genre_name", "raw_text", "search_text", "year"},
        )
        for key in ("id", "title", "overview", "poster", "rating", "release_date"):
            self.assertIn(key, payload["results"][0])

    def test_filter_only_prompt_reports_the_discover_strategy(self):
        payload = self.client.post("/api/search", json={"query": "scary movies from 2022"}).json()

        self.assertEqual(payload["meta"]["strategy"], "discover")
        self.assertEqual(payload["parsed"]["genre_name"], "horror")

    def test_empty_query_is_rejected(self):
        response = self.client.post("/api/search", json={"query": "   "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_query")

    def test_malformed_body_is_rejected_not_crashed(self):
        response = self.client.post(
            "/api/search", content=b"not json", headers={"content-type": "application/json"}
        )

        self.assertEqual(response.status_code, 400)

    def test_upstream_rate_limit_maps_to_429_with_retry_after(self):
        webapp.service = CatalogService(
            FakeGateway(failures={"search": UpstreamRateLimitedError("slow down", retry_after=7)})
        )

        response = self.client.post("/api/search", json={"query": "Dune 2024"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "7")

    def test_inbound_rate_limit_blocks_the_caller(self):
        webapp.rate_limiter.max_requests = 2
        try:
            for _ in range(2):
                self.assertEqual(
                    self.client.post("/api/search", json={"query": "Dune"}).status_code, 200
                )

            blocked = self.client.post("/api/search", json={"query": "Dune"})

            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.json()["error"], "rate_limited")
            self.assertIn("retry-after", blocked.headers)
        finally:
            webapp.rate_limiter.max_requests = 30

    def test_movie_endpoint_returns_the_orchestrated_overview(self):
        response = self.client.get("/api/movies/693134")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["detail"]["title"], "Dune: Part Two")
        self.assertEqual(payload["meta"]["partial"], [])

    def test_movie_endpoint_reports_where_to_watch(self):
        payload = self.client.get("/api/movies/693134?region=IN").json()

        self.assertEqual(payload["watch"]["region"], "IN")
        self.assertEqual(payload["watch"]["stream"][0]["name"], "Netflix")
        self.assertIn("JustWatch", payload["watch"]["attribution"])

    def test_dedicated_watch_endpoint(self):
        response = self.client.get("/api/movies/693134/watch?region=GB")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["region"], "GB")
        self.assertEqual([p["name"] for p in payload["rent"]], ["Apple TV Store"])

    def test_region_defaults_to_the_cdn_geo_header(self):
        self.client.get("/api/movies/693134/watch", headers={"x-vercel-ip-country": "AU"})

        self.assertEqual(self.gateway.watch_regions, ["AU"])

    def test_explicit_region_beats_the_geo_header(self):
        self.client.get(
            "/api/movies/693134/watch?region=JP", headers={"x-vercel-ip-country": "AU"}
        )

        self.assertEqual(self.gateway.watch_regions, ["JP"])

    def test_watch_endpoint_is_rate_limited(self):
        webapp.rate_limiter.max_requests = 1
        try:
            self.assertEqual(
                self.client.get("/api/movies/693134/watch").status_code, 200
            )
            self.assertEqual(
                self.client.get("/api/movies/693134/watch").status_code, 429
            )
        finally:
            webapp.rate_limiter.max_requests = 30

    def test_missing_movie_maps_to_404(self):
        webapp.service = CatalogService(FakeGateway(failures={"movie": NotFoundError("gone")}))

        response = self.client.get("/api/movies/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "not_found")

    def test_non_numeric_movie_id_is_rejected(self):
        self.assertEqual(self.client.get("/api/movies/abc").status_code, 400)

    def test_health_reports_dependency_state(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dependencies"][0]["breaker"]["state"], "closed")

    def test_health_turns_red_when_the_breaker_is_open(self):
        gateway = FakeGateway()
        gateway.snapshot = lambda: {"upstream": "tmdb", "breaker": {"state": "open"}}
        webapp.service = CatalogService(gateway)

        response = self.client.get("/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")

    def test_every_response_carries_a_correlation_id(self):
        response = self.client.get("/health")

        self.assertTrue(response.headers.get("x-request-id"))

    def test_supplied_correlation_id_is_echoed_back(self):
        response = self.client.get("/health", headers={"x-request-id": "abc123"})

        self.assertEqual(response.headers["x-request-id"], "abc123")

    def test_index_page_is_served(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Movie Finder", response.text)


class McpAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gateway = FakeGateway(
            search_results=[summary(693134, "Dune: Part Two", "2024")],
        )
        self._original = server._service
        server._service = CatalogService(self.gateway)

    def tearDown(self):
        server._service = self._original

    async def test_search_tool_formats_results_with_ids(self):
        result = await server.search_movies("Dune 2024")

        self.assertIn("Dune: Part Two", result)
        self.assertIn("ID: 693134", result)

    async def test_search_tool_reports_no_matches(self):
        server._service = CatalogService(FakeGateway(search_results=[]))

        self.assertEqual(await server.search_movies("zzzzzz"), "No movies found.")

    async def test_details_tool_includes_cast_and_similar(self):
        gateway = FakeGateway()

        async def cast(movie_id, limit=8):
            from movieservice.models import CastMember

            return [CastMember(name="Timothée Chalamet", character="Paul Atreides")]

        async def recommendations(movie_id, limit=5):
            return [MovieSummary(id=438631, title="Dune", release_date="2021")]

        gateway.cast = cast
        gateway.recommendations = recommendations
        server._service = CatalogService(gateway)

        result = await server.get_movie_details(693134)

        self.assertIn("Title: Dune: Part Two", result)
        self.assertIn("Timothée Chalamet as Paul Atreides", result)
        self.assertIn("Similar Movies:", result)

    async def test_details_tool_flags_a_degraded_response(self):
        from movieservice.errors import UpstreamUnavailableError

        server._service = CatalogService(
            FakeGateway(failures={"cast": UpstreamUnavailableError("credits down")})
        )

        result = await server.get_movie_details(693134)

        self.assertIn("Title: Dune: Part Two", result)
        self.assertIn("Partial result", result)

    async def test_watch_tool_lists_providers_by_tier(self):
        result = await server.get_watch_providers(693134, "IN")

        self.assertIn("Where To Watch (IN)", result)
        self.assertIn("Stream: Netflix", result)
        self.assertIn("Rent: Apple TV Store", result)
        self.assertIn("JustWatch", result, "TMDB requires the JustWatch attribution")

    async def test_watch_tool_states_the_region_when_nothing_is_listed(self):
        gateway = FakeGateway()

        async def empty(movie_id, region):
            from movieservice.models import WatchAvailability

            return WatchAvailability(region=region)

        gateway.watch_providers = empty
        server._service = CatalogService(gateway)

        result = await server.get_watch_providers(693134, "ZZ")

        self.assertIn("(ZZ)", result, "an empty answer must still name its region")
        self.assertIn("no streaming, rental, or purchase options", result)

    async def test_details_tool_includes_where_to_watch(self):
        result = await server.get_movie_details(693134, "IN")

        self.assertIn("Title: Dune: Part Two", result)
        self.assertIn("Where To Watch (IN)", result)

    async def test_tools_return_readable_text_on_upstream_failure(self):
        from movieservice.errors import UpstreamTimeoutError

        server._service = CatalogService(
            FakeGateway(failures={"search": UpstreamTimeoutError("tmdb timed out")})
        )

        result = await server.search_movies("Dune")

        self.assertIn("Could not search movies", result)
        self.assertIn("timed out", result)

    async def test_registered_tool_names_are_stable(self):
        """Renaming a tool breaks every client already pointed at it."""
        names = {tool.name for tool in await server.mcp.list_tools()}

        self.assertLessEqual(
            {"search_movies", "get_movie_details", "get_recommendations"},
            names,
            "existing tool names must survive; adding new ones is fine",
        )

    async def test_watch_provider_tool_is_registered(self):
        tools = {tool.name: tool for tool in await server.mcp.list_tools()}

        self.assertIn("get_watch_providers", tools)
        self.assertIn("region", tools["get_watch_providers"].inputSchema["properties"])


if __name__ == "__main__":
    unittest.main()
