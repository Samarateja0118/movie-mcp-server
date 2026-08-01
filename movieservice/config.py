"""Environment-driven configuration for every service boundary.

Configuration is read once, in one place, so that the transport, gateway, and
inbound layers never reach into ``os.environ`` themselves. That keeps each layer
independently testable: a test constructs a ``Settings`` instance instead of
mutating global state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration shared by all layers."""

    # Upstream integration
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p/w342"
    tmdb_logo_base: str = "https://image.tmdb.org/t/p/w92"
    tmdb_token: str | None = None

    # Watch availability (JustWatch data, served through TMDB)
    default_watch_region: str = "US"

    # Connection pool / latency budget
    request_timeout: float = 8.0
    connect_timeout: float = 3.0
    pool_max_connections: int = 20
    pool_max_keepalive: int = 10

    # Retry policy
    retry_attempts: int = 3
    retry_base_delay: float = 0.2
    retry_max_delay: float = 2.0
    retry_jitter: float = 0.25

    # Circuit breaker (fault isolation)
    breaker_failure_threshold: int = 5
    breaker_reset_seconds: float = 20.0

    # Response cache
    cache_ttl_seconds: float = 60.0
    cache_genre_ttl_seconds: float = 3600.0
    cache_watch_ttl_seconds: float = 900.0
    cache_max_entries: int = 512

    # Inbound protection
    rate_limit_max_requests: int = 30
    rate_limit_window_seconds: int = 3600
    max_query_length: int = 140

    # Observability
    log_level: str = "INFO"
    service_name: str = "movie-catalog"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            tmdb_base_url=os.getenv("TMDB_BASE_URL", cls.tmdb_base_url),
            tmdb_image_base=os.getenv("TMDB_IMAGE_BASE", cls.tmdb_image_base),
            tmdb_logo_base=os.getenv("TMDB_LOGO_BASE", cls.tmdb_logo_base),
            tmdb_token=os.getenv("TMDB_TOKEN") or None,
            default_watch_region=(
                os.getenv("DEFAULT_WATCH_REGION", cls.default_watch_region).upper()
            ),
            request_timeout=_env_float("REQUEST_TIMEOUT", cls.request_timeout),
            connect_timeout=_env_float("CONNECT_TIMEOUT", cls.connect_timeout),
            pool_max_connections=_env_int("POOL_MAX_CONNECTIONS", cls.pool_max_connections),
            pool_max_keepalive=_env_int("POOL_MAX_KEEPALIVE", cls.pool_max_keepalive),
            retry_attempts=_env_int("RETRY_ATTEMPTS", cls.retry_attempts),
            retry_base_delay=_env_float("RETRY_BASE_DELAY", cls.retry_base_delay),
            retry_max_delay=_env_float("RETRY_MAX_DELAY", cls.retry_max_delay),
            retry_jitter=_env_float("RETRY_JITTER", cls.retry_jitter),
            breaker_failure_threshold=_env_int(
                "BREAKER_FAILURE_THRESHOLD", cls.breaker_failure_threshold
            ),
            breaker_reset_seconds=_env_float("BREAKER_RESET_SECONDS", cls.breaker_reset_seconds),
            cache_ttl_seconds=_env_float("CACHE_TTL_SECONDS", cls.cache_ttl_seconds),
            cache_genre_ttl_seconds=_env_float(
                "CACHE_GENRE_TTL_SECONDS", cls.cache_genre_ttl_seconds
            ),
            cache_watch_ttl_seconds=_env_float(
                "CACHE_WATCH_TTL_SECONDS", cls.cache_watch_ttl_seconds
            ),
            cache_max_entries=_env_int("CACHE_MAX_ENTRIES", cls.cache_max_entries),
            rate_limit_max_requests=_env_int(
                "RATE_LIMIT_MAX_REQUESTS", cls.rate_limit_max_requests
            ),
            rate_limit_window_seconds=_env_int(
                "RATE_LIMIT_WINDOW_SECONDS", cls.rate_limit_window_seconds
            ),
            max_query_length=_env_int("MAX_QUERY_LENGTH", cls.max_query_length),
            log_level=os.getenv("LOG_LEVEL", cls.log_level),
            service_name=os.getenv("SERVICE_NAME", cls.service_name),
        )

    def with_overrides(self, **overrides: object) -> "Settings":
        """Return a copy with fields replaced — handy in tests."""
        return replace(self, **overrides)  # type: ignore[arg-type]
