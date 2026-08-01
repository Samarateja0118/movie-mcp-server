"""Movie catalog service.

Layering, outermost first:

    adapters (server.py MCP, webapp.py HTTP)
        -> catalog.CatalogService     domain logic + orchestration
            -> tmdb.TmdbGateway       TMDB vocabulary, response modelling
                -> transport.*        pooling, retries, breaker, cache

Each arrow points one way. Nothing in ``catalog`` imports ``httpx``; nothing in
``transport`` knows what a movie is.
"""

from __future__ import annotations

from .catalog import CatalogService
from .config import Settings
from .errors import (
    ConfigurationError,
    InvalidQueryError,
    MovieServiceError,
    NotFoundError,
    RateLimitedError,
    UpstreamError,
)
from .inbound import SlidingWindowRateLimiter
from .models import (
    CastMember,
    MovieDetail,
    MovieOverview,
    MovieSummary,
    ParsedQuery,
    SearchResponse,
    WatchAvailability,
    WatchProvider,
)
from .observability import configure_logging, new_request_id, request_id_var
from .tmdb import TmdbGateway

__all__ = [
    "CastMember",
    "CatalogService",
    "ConfigurationError",
    "InvalidQueryError",
    "MovieDetail",
    "MovieOverview",
    "MovieServiceError",
    "MovieSummary",
    "NotFoundError",
    "ParsedQuery",
    "RateLimitedError",
    "SearchResponse",
    "Settings",
    "SlidingWindowRateLimiter",
    "TmdbGateway",
    "UpstreamError",
    "WatchAvailability",
    "WatchProvider",
    "build_service",
    "configure_logging",
    "new_request_id",
    "request_id_var",
]


def build_service(settings: Settings | None = None) -> CatalogService:
    """Compose the service graph. The one place wiring happens."""
    resolved = settings or Settings.from_env()
    return CatalogService(TmdbGateway(resolved), resolved)
