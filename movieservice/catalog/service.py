"""Catalog service — the domain layer both adapters call.

The MCP server and the HTTP API are two transports in front of *this* object.
Neither owns business logic, which is why a change to ranking or orchestration
lands in one file and shows up in both surfaces at once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..config import Settings
from ..errors import InvalidQueryError, MovieServiceError
from ..models import MovieDetail, MovieOverview, MovieSummary, ParsedQuery, SearchResponse
from ..tmdb import TmdbGateway
from .query import parse_query

logger = logging.getLogger("movieservice.catalog")


class CatalogService:
    def __init__(self, gateway: TmdbGateway, settings: Settings | None = None) -> None:
        self.gateway = gateway
        self.settings = settings or gateway.settings

    # -- search ------------------------------------------------------------

    async def search(self, text: str, *, limit: int = 20) -> SearchResponse:
        """Turn a plain-English prompt into ranked results."""
        query = self._require_query(text)
        started = time.monotonic()

        genres = await self._safe_genres()
        parsed = parse_query(query, genres)

        if parsed.is_filter_only:
            strategy = "discover"
            results = await self.gateway.discover_movies(
                genre_id=parsed.genre_id, year=parsed.year
            )
        else:
            strategy = "search"
            results = await self.gateway.search_movies(parsed.search_text, year=parsed.year)
            results = self._narrow(results, parsed)

        return SearchResponse(
            query=query,
            parsed=parsed,
            results=results[:limit],
            strategy=strategy,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    def _narrow(self, results: list[MovieSummary], parsed: ParsedQuery) -> list[MovieSummary]:
        """Prefer exact-ish matches, but never narrow a list down to nothing."""
        needle = parsed.search_text.strip()
        if needle:
            title_matches = [movie for movie in results if needle in movie.title.lower()]
            results = title_matches or results

        if needle and parsed.year:
            year_matches = [
                movie for movie in results if movie.release_date == str(parsed.year)
            ]
            results = year_matches or results

        return results

    # -- single movie ------------------------------------------------------

    async def movie_overview(self, movie_id: int, *, cast_limit: int = 8) -> MovieOverview:
        """Fetch a movie, its cast, and its neighbours as one concurrent unit.

        Three upstream calls issued together cost roughly one round trip instead
        of three. The detail call is required; cast and recommendations are
        enrichment, so a failure there degrades the response instead of failing
        it — that partial state is reported back rather than hidden.
        """
        started = time.monotonic()

        detail_task = asyncio.create_task(self.gateway.movie(movie_id))
        cast_task = asyncio.create_task(self.gateway.cast(movie_id, limit=cast_limit))
        similar_task = asyncio.create_task(self.gateway.recommendations(movie_id))

        results = await asyncio.gather(
            detail_task, cast_task, similar_task, return_exceptions=True
        )
        detail, cast, similar = results

        if isinstance(detail, BaseException):
            raise detail  # the required leg failed; there is no useful response

        partial: list[str] = []
        if isinstance(cast, BaseException):
            partial.append("cast")
            self._log_degraded("cast", movie_id, cast)
            cast = []
        if isinstance(similar, BaseException):
            partial.append("similar")
            self._log_degraded("similar", movie_id, similar)
            similar = []

        return MovieOverview(
            detail=detail,
            cast=cast,
            similar=similar,
            partial=partial,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    async def movie_detail(self, movie_id: int) -> MovieDetail:
        return await self.gateway.movie(movie_id)

    async def recommendations(self, movie_id: int, *, limit: int = 5) -> list[MovieSummary]:
        return await self.gateway.recommendations(movie_id, limit=limit)

    # -- health ------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "service": self.settings.service_name,
            "dependencies": [self.gateway.snapshot()],
        }

    # -- helpers -----------------------------------------------------------

    def _require_query(self, text: str) -> str:
        query = (text or "").strip()
        if not query:
            raise InvalidQueryError("query required")
        if len(query) > self.settings.max_query_length:
            raise InvalidQueryError(
                f"Keep your query under {self.settings.max_query_length} characters."
            )
        return query

    async def _safe_genres(self) -> dict[str, int]:
        """Genre lookup is an optimisation; without it we fall back to title search."""
        try:
            return await self.gateway.genres()
        except MovieServiceError as exc:
            logger.warning("genre_lookup_failed", extra={"reason": exc.detail})
            return {}

    @staticmethod
    def _log_degraded(leg: str, movie_id: int, exc: BaseException) -> None:
        logger.warning(
            "overview_leg_failed",
            extra={"leg": leg, "movie_id": movie_id, "reason": repr(exc)},
        )
