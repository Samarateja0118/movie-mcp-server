"""Domain error taxonomy.

Every layer below the adapters raises one of these. Adapters (MCP, HTTP) are the
only places that decide how an error is *presented* — as JSON, as an HTTP
status, or as MCP tool text. Upstream failure modes (``httpx`` exceptions, TMDB
status codes) never leak past the transport layer, which is what lets the
gateway be swapped without touching callers.
"""

from __future__ import annotations

from typing import Any


class MovieServiceError(Exception):
    """Base class for every error this service raises on purpose."""

    code = "service_error"
    status_code = 500
    retryable = False

    def __init__(self, detail: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.context = context or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": self.code, "detail": self.detail}
        if self.context:
            payload["context"] = self.context
        return payload


class ConfigurationError(MovieServiceError):
    """The service is missing something it needs to start doing work."""

    code = "configuration_error"
    status_code = 500


class InvalidQueryError(MovieServiceError):
    """The caller sent something this service will not act on."""

    code = "invalid_query"
    status_code = 400


class NotFoundError(MovieServiceError):
    """The upstream catalog has no such record."""

    code = "not_found"
    status_code = 404


class RateLimitedError(MovieServiceError):
    """The *caller* exceeded this service's inbound quota."""

    code = "rate_limited"
    status_code = 429

    def __init__(self, detail: str, *, retry_after: int = 1, **kwargs: Any) -> None:
        super().__init__(detail, **kwargs)
        self.retry_after = retry_after


class UpstreamError(MovieServiceError):
    """Base for anything that went wrong on the other side of the boundary."""

    code = "upstream_error"
    status_code = 502


class UpstreamRateLimitedError(UpstreamError):
    """TMDB throttled us; retries were exhausted."""

    code = "upstream_rate_limited"
    status_code = 429
    retryable = True

    def __init__(self, detail: str, *, retry_after: int = 1, **kwargs: Any) -> None:
        super().__init__(detail, **kwargs)
        self.retry_after = retry_after


class UpstreamTimeoutError(UpstreamError):
    """We gave up waiting on TMDB."""

    code = "upstream_timeout"
    status_code = 504
    retryable = True


class UpstreamUnavailableError(UpstreamError):
    """TMDB is refusing connections or returning 5xx."""

    code = "upstream_unavailable"
    status_code = 502
    retryable = True


class CircuitOpenError(UpstreamError):
    """We are deliberately not calling TMDB right now, to let it recover."""

    code = "circuit_open"
    status_code = 503
    retryable = True
