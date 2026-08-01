"""Domain layer: prompt parsing, gateway mapping, and service orchestration."""

from __future__ import annotations

import asyncio
import unittest

import httpx

from movieservice import Settings
from movieservice.catalog import CatalogService, parse_query
from movieservice.errors import InvalidQueryError, NotFoundError, UpstreamUnavailableError
from movieservice.models import (
    MovieDetail,
    MovieSummary,
    WatchAvailability,
    WatchProvider,
)
from movieservice.tmdb import TmdbGateway
from movieservice.transport import ResilientHttpClient, RetryPolicy

GENRES = {
    "action": 28,
    "animation": 16,
    "comedy": 35,
    "family": 10751,
    "horror": 27,
    "romance": 10749,
    "science fiction": 878,
    "thriller": 53,
}


class ParseQueryTests(unittest.TestCase):
    def test_keeps_title_and_year(self):
        parsed = parse_query("Dune 2024", GENRES)

        self.assertEqual(parsed.search_text, "dune")
        self.assertEqual(parsed.year, 2024)
        self.assertIsNone(parsed.genre_id)
        self.assertFalse(parsed.is_filter_only)

    def test_detects_filter_only_requests(self):
        parsed = parse_query("scary movies from 2022", GENRES)

        self.assertEqual(parsed.genre_id, 27)
        self.assertEqual(parsed.genre_name, "horror")
        self.assertEqual(parsed.search_text, "")
        self.assertEqual(parsed.year, 2022)
        self.assertTrue(parsed.is_filter_only)

    def test_expands_genre_synonyms(self):
        self.assertEqual(parse_query("funny movies", GENRES).genre_name, "comedy")
        self.assertEqual(parse_query("sci-fi 2024", GENRES).genre_name, "science fiction")
        self.assertEqual(parse_query("animated family 2023", GENRES).genre_id, 16)

    def test_strips_request_filler_words(self):
        parsed = parse_query("show me the best inception films", GENRES)

        self.assertEqual(parsed.search_text, "inception")

    def test_survives_an_empty_genre_map(self):
        parsed = parse_query("Dune 2024", {})

        self.assertEqual(parsed.search_text, "dune")
        self.assertIsNone(parsed.genre_name)


def build_gateway(handler, settings: Settings | None = None) -> TmdbGateway:
    resolved = (settings or Settings()).with_overrides(tmdb_token="test-token")

    async def no_sleep(_delay: float) -> None:
        return None

    client = ResilientHttpClient(
        resolved.tmdb_base_url,
        headers_provider=lambda: {"Authorization": "Bearer test-token"},
        retry=RetryPolicy(attempts=2, jitter=0),
        sleep=no_sleep,
        name="tmdb",
    )
    client._client = httpx.AsyncClient(
        base_url=resolved.tmdb_base_url, transport=httpx.MockTransport(handler)
    )
    return TmdbGateway(resolved, client)


SEARCH_PAYLOAD = {
    "results": [
        {
            "id": 693134,
            "title": "Dune: Part Two",
            "overview": "Paul returns to Arrakis.",
            "poster_path": "/poster.jpg",
            "release_date": "2024-02-27",
            "vote_average": 8.147,
            "popularity": 900.5,
        },
        {
            "id": 438631,
            "title": "Dune",
            "release_date": "2021-09-15",
            "vote_average": 7.8,
        },
    ]
}

DETAIL_PAYLOAD = {
    "id": 693134,
    "title": "Dune: Part Two",
    "overview": "Paul returns to Arrakis.",
    "poster_path": "/poster.jpg",
    "release_date": "2024-02-27",
    "runtime": 167,
    "tagline": "Long live the fighters.",
    "genres": [{"name": "Science Fiction"}, {"name": "Adventure"}],
    "vote_average": 8.1,
}

PROVIDERS_PAYLOAD = {
    "id": 693134,
    "results": {
        "US": {
            "link": "https://www.themoviedb.org/movie/693134/watch?locale=US",
            "flatrate": [
                {"provider_name": "HBO Max", "logo_path": "/hbo.jpg", "display_priority": 3}
            ],
            "ads": [
                {"provider_name": "Freevee", "logo_path": "/freevee.jpg", "display_priority": 9}
            ],
            "buy": [
                {"provider_name": "Amazon Video", "logo_path": "/amazon.jpg", "display_priority": 8}
            ],
        },
        "IN": {
            "link": "https://www.themoviedb.org/movie/693134/watch?locale=IN",
            "flatrate": [
                {"provider_name": "Netflix", "logo_path": "/netflix.jpg", "display_priority": 0}
            ],
            "rent": [
                {"provider_name": "Apple TV Store", "logo_path": "/apple.jpg", "display_priority": 5}
            ],
        },
    },
}

CREDITS_PAYLOAD = {
    "cast": [
        {"name": "Zendaya", "character": "Chani", "order": 1},
        {"name": "Timothée Chalamet", "character": "Paul Atreides", "order": 0},
    ]
}


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_search_payload_into_models(self):
        gateway = build_gateway(lambda request: httpx.Response(200, json=SEARCH_PAYLOAD))

        results = await gateway.search_movies("dune", year=2024)

        self.assertIsInstance(results[0], MovieSummary)
        self.assertEqual(results[0].title, "Dune: Part Two")
        self.assertEqual(results[0].release_date, "2024")
        self.assertEqual(results[0].rating, 8.1, "vote_average is rounded for display")
        self.assertEqual(results[0].poster, "https://image.tmdb.org/t/p/w342/poster.jpg")
        self.assertIsNone(results[1].poster, "a missing poster_path yields no URL")
        await gateway.aclose()

    async def test_maps_detail_payload_into_models(self):
        gateway = build_gateway(lambda request: httpx.Response(200, json=DETAIL_PAYLOAD))

        detail = await gateway.movie(693134)

        self.assertIsInstance(detail, MovieDetail)
        self.assertEqual(detail.runtime_minutes, 167)
        self.assertEqual(detail.genres, ["Science Fiction", "Adventure"])
        self.assertEqual(detail.tagline, "Long live the fighters.")
        await gateway.aclose()

    async def test_cast_is_ordered_by_billing_and_capped(self):
        gateway = build_gateway(lambda request: httpx.Response(200, json=CREDITS_PAYLOAD))

        cast = await gateway.cast(693134, limit=1)

        self.assertEqual([member.name for member in cast], ["Timothée Chalamet"])
        await gateway.aclose()

    async def test_genre_list_is_cached_across_calls(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"genres": [{"id": 27, "name": "Horror"}]})

        gateway = build_gateway(handler)

        first = await gateway.genres()
        second = await gateway.genres()

        self.assertEqual(first, {"horror": 27})
        self.assertEqual(second, first)
        self.assertEqual(calls, 1)
        await gateway.aclose()

    async def test_watch_providers_selects_the_requested_region(self):
        gateway = build_gateway(lambda request: httpx.Response(200, json=PROVIDERS_PAYLOAD))

        watch = await gateway.watch_providers(693134, "in")

        self.assertEqual(watch.region, "IN", "region codes are normalised to upper case")
        self.assertEqual([p.name for p in watch.stream], ["Netflix"])
        self.assertEqual([p.name for p in watch.rent], ["Apple TV Store"])
        self.assertIn("locale=IN", watch.link)
        await gateway.aclose()

    async def test_watch_providers_merge_ad_supported_into_stream(self):
        gateway = build_gateway(lambda request: httpx.Response(200, json=PROVIDERS_PAYLOAD))

        watch = await gateway.watch_providers(693134, "US")

        # `flatrate` and `ads` both mean "press play", ordered by TMDB priority.
        self.assertEqual([p.name for p in watch.stream], ["HBO Max", "Freevee"])
        self.assertEqual([p.name for p in watch.buy], ["Amazon Video"])
        await gateway.aclose()

    async def test_unknown_region_yields_an_empty_but_valid_answer(self):
        gateway = build_gateway(lambda request: httpx.Response(200, json=PROVIDERS_PAYLOAD))

        watch = await gateway.watch_providers(693134, "ZZ")

        self.assertTrue(watch.is_empty)
        self.assertEqual(watch.region, "ZZ")
        self.assertIsNone(watch.link)
        await gateway.aclose()

    async def test_one_upstream_call_answers_every_region(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=PROVIDERS_PAYLOAD)

        gateway = build_gateway(handler)

        us = await gateway.watch_providers(693134, "US")
        india = await gateway.watch_providers(693134, "IN")

        self.assertEqual(calls, 1, "TMDB returns all regions; cache by movie, not region")
        self.assertNotEqual(us.stream[0].name, india.stream[0].name)
        await gateway.aclose()

    async def test_provider_logos_use_the_logo_size_base(self):
        gateway = build_gateway(lambda request: httpx.Response(200, json=PROVIDERS_PAYLOAD))

        watch = await gateway.watch_providers(693134, "IN")

        self.assertEqual(watch.stream[0].logo, "https://image.tmdb.org/t/p/w92/netflix.jpg")
        await gateway.aclose()

    async def test_upstream_errors_do_not_leak_httpx_types(self):
        gateway = build_gateway(lambda request: httpx.Response(500))

        with self.assertRaises(UpstreamUnavailableError):
            await gateway.movie(1)
        await gateway.aclose()


class FakeGateway:
    """A stand-in for the TMDB boundary — proof the service depends on the
    interface rather than on TMDB itself."""

    def __init__(self, **overrides):
        self.settings = Settings().with_overrides(tmdb_token="test-token")
        self.calls: list[str] = []
        self.delays: dict[str, float] = overrides.pop("delays", {})
        self.failures: dict[str, Exception] = overrides.pop("failures", {})
        self.genre_map = overrides.pop("genres", GENRES)
        self.search_results = overrides.pop("search_results", [])
        self.discover_results = overrides.pop("discover_results", [])
        self.watch_regions: list[str] = []

    async def _run(self, name: str, value):
        self.calls.append(name)
        await asyncio.sleep(self.delays.get(name, 0))
        if name in self.failures:
            raise self.failures[name]
        return value

    async def genres(self):
        return await self._run("genres", self.genre_map)

    async def search_movies(self, query, *, year=None, page=1):
        return await self._run("search", list(self.search_results))

    async def discover_movies(self, *, genre_id=None, year=None, page=1):
        return await self._run("discover", list(self.discover_results))

    async def movie(self, movie_id):
        return await self._run(
            "movie", MovieDetail(id=movie_id, title="Dune: Part Two", release_date="2024-02-27")
        )

    async def cast(self, movie_id, limit=8):
        return await self._run("cast", [])

    async def recommendations(self, movie_id, limit=5):
        return await self._run("recommendations", [])

    async def watch_providers(self, movie_id, region):
        self.watch_regions.append(region)
        return await self._run(
            "watch",
            WatchAvailability(
                region=region,
                link=f"https://www.themoviedb.org/movie/{movie_id}/watch?locale={region}",
                stream=[WatchProvider(name="Netflix")],
                rent=[WatchProvider(name="Apple TV Store")],
            ),
        )

    def snapshot(self):
        return {"upstream": "fake", "breaker": {"state": "closed"}}


def summary(movie_id: int, title: str, year: str) -> MovieSummary:
    return MovieSummary(id=movie_id, title=title, release_date=year)


class CatalogServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_empty_query(self):
        service = CatalogService(FakeGateway())

        with self.assertRaises(InvalidQueryError):
            await service.search("   ")

    async def test_rejects_overlong_query(self):
        gateway = FakeGateway()
        service = CatalogService(gateway, gateway.settings.with_overrides(max_query_length=10))

        with self.assertRaises(InvalidQueryError):
            await service.search("x" * 11)

    async def test_filter_only_prompt_uses_discover(self):
        gateway = FakeGateway(discover_results=[summary(1, "Smile", "2022")])
        service = CatalogService(gateway)

        response = await service.search("scary movies from 2022")

        self.assertEqual(response.strategy, "discover")
        self.assertIn("discover", gateway.calls)
        self.assertNotIn("search", gateway.calls)
        self.assertEqual(response.parsed.genre_name, "horror")

    async def test_title_prompt_prefers_matching_year(self):
        gateway = FakeGateway(
            search_results=[
                summary(438631, "Dune", "2021"),
                summary(693134, "Dune: Part Two", "2024"),
            ]
        )
        service = CatalogService(gateway)

        response = await service.search("Dune 2024")

        self.assertEqual(response.strategy, "search")
        self.assertEqual([m.id for m in response.results], [693134])

    async def test_narrowing_never_empties_the_result_list(self):
        gateway = FakeGateway(search_results=[summary(1, "Dune", "2021")])
        service = CatalogService(gateway)

        response = await service.search("Dune 1999")

        self.assertEqual(len(response.results), 1, "a strict year filter must not blank the page")

    async def test_search_survives_a_genre_lookup_outage(self):
        gateway = FakeGateway(
            failures={"genres": UpstreamUnavailableError("tmdb down")},
            search_results=[summary(1, "Dune", "2021")],
        )
        service = CatalogService(gateway)

        response = await service.search("Dune")

        self.assertEqual(len(response.results), 1)
        self.assertIsNone(response.parsed.genre_name)

    async def test_overview_fans_out_concurrently(self):
        gateway = FakeGateway(delays={"movie": 0.05, "cast": 0.05, "recommendations": 0.05})
        service = CatalogService(gateway)

        started = asyncio.get_running_loop().time()
        overview = await service.movie_overview(693134)
        elapsed = asyncio.get_running_loop().time() - started

        self.assertEqual(overview.detail.title, "Dune: Part Two")
        self.assertLess(
            elapsed, 0.12, "three 50ms calls run together should not cost 150ms"
        )

    async def test_overview_degrades_when_enrichment_fails(self):
        gateway = FakeGateway(
            failures={
                "cast": UpstreamUnavailableError("credits down"),
                "recommendations": UpstreamUnavailableError("recs down"),
            }
        )
        service = CatalogService(gateway)

        overview = await service.movie_overview(693134)

        self.assertEqual(overview.detail.id, 693134)
        self.assertEqual(overview.cast, [])
        self.assertEqual(sorted(overview.partial), ["cast", "similar"])

    async def test_overview_fails_when_the_required_call_fails(self):
        gateway = FakeGateway(failures={"movie": NotFoundError("no such movie")})
        service = CatalogService(gateway)

        with self.assertRaises(NotFoundError):
            await service.movie_overview(1)

    async def test_overview_includes_watch_availability(self):
        gateway = FakeGateway()
        service = CatalogService(gateway)

        overview = await service.movie_overview(693134, region="IN")

        self.assertEqual(overview.watch.region, "IN")
        self.assertEqual([p.name for p in overview.watch.stream], ["Netflix"])
        self.assertEqual(gateway.watch_regions, ["IN"])

    async def test_overview_survives_a_watch_provider_outage(self):
        gateway = FakeGateway(failures={"watch": UpstreamUnavailableError("providers down")})
        service = CatalogService(gateway)

        overview = await service.movie_overview(693134)

        self.assertIsNone(overview.watch)
        self.assertEqual(overview.partial, ["watch"])
        self.assertEqual(overview.detail.title, "Dune: Part Two", "the movie still loads")

    async def test_watch_leg_joins_the_concurrent_fan_out(self):
        gateway = FakeGateway(
            delays={"movie": 0.05, "cast": 0.05, "recommendations": 0.05, "watch": 0.05}
        )
        service = CatalogService(gateway)

        started = asyncio.get_running_loop().time()
        await service.movie_overview(693134)
        elapsed = asyncio.get_running_loop().time() - started

        self.assertLess(elapsed, 0.12, "a fourth leg should not add a fourth round trip")

    async def test_region_falls_back_to_the_configured_default(self):
        gateway = FakeGateway()
        service = CatalogService(gateway, gateway.settings.with_overrides(default_watch_region="GB"))

        for supplied in (None, "", "  ", "x", "USA", "12"):
            await service.where_to_watch(693134, region=supplied)

        self.assertEqual(gateway.watch_regions, ["GB"] * 6, "malformed regions must not reach TMDB")

    async def test_region_is_normalised(self):
        gateway = FakeGateway()
        service = CatalogService(gateway)

        await service.where_to_watch(693134, region=" in ")

        self.assertEqual(gateway.watch_regions, ["IN"])


if __name__ == "__main__":
    unittest.main()
