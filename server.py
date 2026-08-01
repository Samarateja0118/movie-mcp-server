"""MCP adapter.

A transport in front of ``CatalogService``: it converts tool arguments into
service calls and service models into text an assistant can read. No TMDB
knowledge, no HTTP handling, no retry logic lives here.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from movieservice import (
    CatalogService,
    MovieServiceError,
    Settings,
    build_service,
    configure_logging,
)
from movieservice.models import MovieOverview, MovieSummary

load_dotenv()

settings = Settings.from_env()
configure_logging(settings.log_level)
logger = logging.getLogger("movieservice.mcp")

mcp = FastMCP("Movie Assistant")

_service: CatalogService | None = None


def get_service() -> CatalogService:
    """Build the service once and reuse it, so the connection pool survives."""
    global _service
    if _service is None:
        _service = build_service(settings)
    return _service


def _format_summaries(movies: list[MovieSummary], *, with_ids: bool = True) -> str:
    lines = []
    for movie in movies:
        year = movie.release_date or "N/A"
        line = f"- {movie.title} ({year}) | Rating: {movie.rating or 'N/A'}"
        if with_ids:
            line += f" | ID: {movie.id}"
        lines.append(line)
    return "\n".join(lines)


def _format_overview(overview: MovieOverview) -> str:
    detail = overview.detail
    sections = [
        f"Title: {detail.title}",
        f"Release Date: {detail.release_date or 'Unknown'}",
        f"Rating: {detail.rating}/10",
        f"Runtime: {detail.runtime_minutes or 'Unknown'} minutes",
        f"Genres: {', '.join(detail.genres) or 'Unknown'}",
        f"Overview: {detail.overview or 'No synopsis available.'}",
    ]
    if detail.tagline:
        sections.insert(1, f"Tagline: {detail.tagline}")
    if overview.cast:
        cast = ", ".join(
            f"{member.name} as {member.character}" if member.character else member.name
            for member in overview.cast
        )
        sections.append(f"Top Cast: {cast}")
    if overview.similar:
        sections.append("Similar Movies:\n" + _format_summaries(overview.similar))
    if overview.partial:
        sections.append(
            f"(Partial result: {', '.join(overview.partial)} unavailable right now.)"
        )
    return "\n".join(sections)


@mcp.tool()
async def search_movies(query: str) -> str:
    """Search for movies by title, genre, mood, or year."""
    try:
        response = await get_service().search(query, limit=5)
    except MovieServiceError as exc:
        return f"Could not search movies: {exc.detail}"

    if not response.results:
        return "No movies found."
    return _format_summaries(response.results)


@mcp.tool()
async def get_movie_details(movie_id: int) -> str:
    """Get details, top cast, and similar titles for a movie by its TMDB ID.

    Runs the three upstream lookups concurrently, so it costs about one round
    trip rather than three.
    """
    try:
        overview = await get_service().movie_overview(movie_id)
    except MovieServiceError as exc:
        return f"Could not load movie {movie_id}: {exc.detail}"

    return _format_overview(overview)


@mcp.tool()
async def get_recommendations(movie_id: int) -> str:
    """Get movie recommendations based on a TMDB movie ID."""
    try:
        movies = await get_service().recommendations(movie_id, limit=5)
    except MovieServiceError as exc:
        return f"Could not load recommendations for {movie_id}: {exc.detail}"

    if not movies:
        return "No recommendations found."
    return _format_summaries(movies)


if __name__ == "__main__":
    logger.info("mcp_server_starting", extra={"transport": "stdio"})
    mcp.run(transport="stdio")
