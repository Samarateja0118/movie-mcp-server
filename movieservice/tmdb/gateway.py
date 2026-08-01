"""TMDB gateway — the only module that knows TMDB's URLs and wire format.

This is the anti-corruption layer. Above it, callers speak in ``MovieSummary``
and ``MovieDetail``; below it, everything is TMDB's vocabulary. Replacing TMDB
with another catalog provider means writing a second gateway with the same
methods, and nothing above changes.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..errors import ConfigurationError
from ..models import CastMember, MovieDetail, MovieSummary, WatchAvailability
from ..transport import CircuitBreaker, ResilientHttpClient, RetryPolicy, TTLCache


class TmdbGateway:
    def __init__(self, settings: Settings, client: ResilientHttpClient | None = None) -> None:
        self.settings = settings
        self.client = client or self._build_client(settings)

    @staticmethod
    def _build_client(settings: Settings) -> ResilientHttpClient:
        def headers() -> dict[str, str]:
            token = settings.tmdb_token
            if not token:
                raise ConfigurationError(
                    "TMDB_TOKEN is not set. Copy .env.example to .env and add your "
                    "TMDB read access token."
                )
            return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        return ResilientHttpClient(
            settings.tmdb_base_url,
            headers_provider=headers,
            timeout=settings.request_timeout,
            connect_timeout=settings.connect_timeout,
            max_connections=settings.pool_max_connections,
            max_keepalive=settings.pool_max_keepalive,
            retry=RetryPolicy(
                attempts=settings.retry_attempts,
                base_delay=settings.retry_base_delay,
                max_delay=settings.retry_max_delay,
                jitter=settings.retry_jitter,
            ),
            breaker=CircuitBreaker(
                failure_threshold=settings.breaker_failure_threshold,
                reset_seconds=settings.breaker_reset_seconds,
                name="tmdb",
            ),
            cache=TTLCache(
                max_entries=settings.cache_max_entries,
                default_ttl=settings.cache_ttl_seconds,
            ),
            name="tmdb",
        )

    # -- reads -------------------------------------------------------------

    async def genres(self) -> dict[str, int]:
        """Genre name -> id. Cached aggressively; this list changes yearly at most."""
        payload = await self.client.get_json(
            "/genre/movie/list",
            {"language": "en-US"},
            cache_ttl=self.settings.cache_genre_ttl_seconds,
        )
        return {
            genre["name"].lower(): genre["id"]
            for genre in payload.get("genres", [])
            if genre.get("name") and genre.get("id") is not None
        }

    async def search_movies(
        self, query: str, *, year: int | None = None, page: int = 1
    ) -> list[MovieSummary]:
        params: dict[str, Any] = {"language": "en-US", "page": page, "query": query}
        if year:
            params["year"] = year
        payload = await self.client.get_json(
            "/search/movie", params, cache_ttl=self.settings.cache_ttl_seconds
        )
        return self._summaries(payload)

    async def discover_movies(
        self, *, genre_id: int | None = None, year: int | None = None, page: int = 1
    ) -> list[MovieSummary]:
        params: dict[str, Any] = {
            "language": "en-US",
            "page": page,
            "sort_by": "popularity.desc",
        }
        if genre_id:
            params["with_genres"] = genre_id
        if year:
            params["primary_release_year"] = year
        payload = await self.client.get_json(
            "/discover/movie", params, cache_ttl=self.settings.cache_ttl_seconds
        )
        return self._summaries(payload)

    async def movie(self, movie_id: int) -> MovieDetail:
        payload = await self.client.get_json(
            f"/movie/{movie_id}",
            {"language": "en-US"},
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        return MovieDetail.from_tmdb(payload, image_base=self.settings.tmdb_image_base)

    async def cast(self, movie_id: int, limit: int = 8) -> list[CastMember]:
        payload = await self.client.get_json(
            f"/movie/{movie_id}/credits",
            {"language": "en-US"},
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        members = [CastMember.from_tmdb(item) for item in payload.get("cast", [])]
        members.sort(key=lambda member: member.order)
        return members[:limit]

    async def watch_providers(self, movie_id: int, region: str) -> WatchAvailability:
        """Where a movie can be watched in ``region``.

        TMDB returns every region in one document, so the cache key is the movie
        rather than the movie-and-region: one upstream call answers a US viewer
        and an IN viewer alike. Availability moves slowly, so it is held longer
        than ordinary search results.
        """
        payload = await self.client.get_json(
            f"/movie/{movie_id}/watch/providers",
            cache_ttl=self.settings.cache_watch_ttl_seconds,
        )
        normalized = region.strip().upper()
        return WatchAvailability.from_tmdb(
            payload.get("results", {}).get(normalized, {}),
            region=normalized,
            logo_base=self.settings.tmdb_logo_base,
        )

    async def recommendations(self, movie_id: int, limit: int = 5) -> list[MovieSummary]:
        payload = await self.client.get_json(
            f"/movie/{movie_id}/recommendations",
            {"language": "en-US", "page": 1},
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        return self._summaries(payload)[:limit]

    # -- helpers -----------------------------------------------------------

    def _summaries(self, payload: dict[str, Any]) -> list[MovieSummary]:
        image_base = self.settings.tmdb_image_base
        return [
            MovieSummary.from_tmdb(item, image_base=image_base)
            for item in payload.get("results", [])
            if item.get("id") is not None
        ]

    def snapshot(self) -> dict[str, Any]:
        return self.client.snapshot()

    async def aclose(self) -> None:
        await self.client.aclose()
