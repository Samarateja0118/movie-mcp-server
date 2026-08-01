"""HTTP adapter (FastAPI / ASGI).

The second transport in front of ``CatalogService``. It owns request parsing,
rate limiting, correlation ids, and status-code mapping — and nothing else. The
JSON contract matches the original Flask version, so the browser client in
``public/static/app.js`` works against it untouched.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from movieservice import (
    CastMember,
    MovieDetail,
    MovieServiceError,
    MovieSummary,
    ParsedQuery,
    RateLimitedError,
    Settings,
    SlidingWindowRateLimiter,
    build_service,
    configure_logging,
    new_request_id,
    request_id_var,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML = BASE_DIR / "templates" / "index.html"
STATIC_DIR = BASE_DIR / "public" / "static"

settings = Settings.from_env()
configure_logging(settings.log_level)
logger = logging.getLogger("movieservice.http")

service = build_service(settings)
rate_limiter = SlidingWindowRateLimiter(
    max_requests=settings.rate_limit_max_requests,
    window_seconds=settings.rate_limit_window_seconds,
)


# -- request/response schemas ---------------------------------------------
# Declared as models so the generated OpenAPI document describes the real
# contract rather than an opaque dict.


class SearchRequest(BaseModel):
    query: str = Field(default="", description="A plain-English movie prompt.")


class SearchMeta(BaseModel):
    strategy: str = Field(description="Which upstream strategy answered: search or discover.")
    elapsed_ms: int


class SearchApiResponse(BaseModel):
    query: str
    parsed: ParsedQuery
    results: list[MovieSummary]
    meta: SearchMeta


class MovieMeta(BaseModel):
    partial: list[str] = Field(
        default_factory=list,
        description="Enrichment legs that failed; the response is degraded, not wrong.",
    )
    elapsed_ms: int


class MovieApiResponse(BaseModel):
    detail: MovieDetail
    cast: list[CastMember]
    similar: list[MovieSummary]
    meta: MovieMeta


class ErrorResponse(BaseModel):
    error: str
    detail: str


# -- lifecycle -------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Hold the connection pool open for the life of the process."""
    logger.info("http_service_starting", extra={"service": settings.service_name})
    try:
        yield
    finally:
        await service.gateway.aclose()
        logger.info("http_service_stopped")


app = FastAPI(
    title="Movie Catalog Service",
    description=(
        "Async TMDB-backed catalog with retries, circuit breaking, and response "
        "caching. The same service layer also runs behind an MCP server."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


# -- middleware ------------------------------------------------------------


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Tag every request with a correlation id and log how long it took."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or new_request_id()
        token = request_id_var.set(request_id)
        started = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        duration_ms = int((time.monotonic() - started) * 1000)
        response.headers["x-request-id"] = request_id
        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        )
        return response


app.add_middleware(RequestContextMiddleware)


# -- error mapping ---------------------------------------------------------


@app.exception_handler(MovieServiceError)
async def handle_service_error(_request: Request, exc: MovieServiceError) -> JSONResponse:
    """One place decides how a domain error becomes an HTTP response."""
    headers = {}
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    logger.warning("request_failed", extra={"error": exc.code, "detail": exc.detail})
    return JSONResponse(exc.to_payload(), status_code=exc.status_code, headers=headers)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Report malformed input as 400 in this service's own error shape."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", ())[1:]) or "body"
    return JSONResponse(
        {"error": "invalid_query", "detail": f"{field}: {first.get('msg', 'invalid request')}"},
        status_code=400,
    )


# -- dependencies ----------------------------------------------------------


async def enforce_rate_limit(request: Request) -> None:
    """Per-IP inbound quota, applied to the endpoints that cost upstream calls."""
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )

    allowed, retry_after = rate_limiter.check(client_ip)
    if not allowed:
        raise RateLimitedError(
            "This live demo is temporarily busy. Please try again a little later.",
            retry_after=retry_after,
        )


# -- routes ----------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index() -> Response:
    return FileResponse(INDEX_HTML)


@app.get("/health", tags=["ops"])
@app.get("/api/health", tags=["ops"])
async def healthcheck() -> JSONResponse:
    """Liveness plus a readable view of dependency health."""
    snapshot = service.snapshot()
    degraded = any(
        dependency["breaker"]["state"] != "closed" for dependency in snapshot["dependencies"]
    )
    return JSONResponse(
        {
            "ok": not degraded,
            "service": settings.service_name,
            "status": "degraded" if degraded else "healthy",
            "rate_limiter": rate_limiter.stats(),
            **snapshot,
        },
        status_code=503 if degraded else 200,
    )


@app.post(
    "/api/search",
    response_model=SearchApiResponse,
    responses={400: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
    dependencies=[Depends(enforce_rate_limit)],
    tags=["catalog"],
)
async def api_search(payload: SearchRequest) -> SearchApiResponse:
    """Turn a plain-English prompt into ranked movie results."""
    response = await service.search(payload.query)
    return SearchApiResponse(
        query=response.query,
        parsed=response.parsed,
        results=response.results,
        meta=SearchMeta(strategy=response.strategy, elapsed_ms=response.elapsed_ms),
    )


@app.get(
    "/api/movies/{movie_id}",
    response_model=MovieApiResponse,
    responses={404: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
    dependencies=[Depends(enforce_rate_limit)],
    tags=["catalog"],
)
async def api_movie(movie_id: int) -> MovieApiResponse:
    """Detail, cast, and similar titles — fetched concurrently as one unit."""
    overview = await service.movie_overview(movie_id)
    return MovieApiResponse(
        detail=overview.detail,
        cast=overview.cast,
        similar=overview.similar,
        meta=MovieMeta(partial=overview.partial, elapsed_ms=overview.elapsed_ms),
    )


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "webapp:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        reload=bool(os.getenv("RELOAD")),
    )
