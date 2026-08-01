# Movie Catalog Service

An asynchronous Python backend that fronts the TMDB catalog behind two independent
transports: an **MCP server** for assistants and an **HTTP/JSON API** for the live
web demo. Both surfaces call the same domain service, so behaviour, caching,
retries, and failure handling are identical no matter which door a caller comes
through.

## Architecture

```
        MCP adapter (server.py)          HTTP adapter (webapp.py)
                 │                                 │
                 └────────────┬────────────────────┘
                              ▼
                 catalog.CatalogService          domain logic, orchestration,
                              │                  degradation policy
                              ▼
                   tmdb.TmdbGateway              TMDB vocabulary → typed models
                              │
                              ▼
                    transport.*                  pooling, retries, breaker, cache
                              │
                              ▼
                          TMDB API
```

Dependencies point one way only. Nothing in `catalog/` imports `httpx`; nothing in
`transport/` knows what a movie is. That is what makes the layers independently
testable and independently replaceable — swapping TMDB for another catalog means
writing a second gateway with the same methods and changing nothing above it.

| Module | Responsibility |
| --- | --- |
| `movieservice/config.py` | All environment reading, in one immutable `Settings` object |
| `movieservice/errors.py` | Domain error taxonomy; upstream failures never leak past transport |
| `movieservice/models.py` | Pydantic response models — the structured pipeline |
| `movieservice/transport/retry.py` | Retry classification and jittered exponential backoff |
| `movieservice/transport/breaker.py` | Circuit breaker (fault isolation per dependency) |
| `movieservice/transport/cache.py` | TTL cache with single-flight de-duplication |
| `movieservice/transport/client.py` | Pooled async HTTP client with all of the above applied |
| `movieservice/tmdb/gateway.py` | The only module that knows TMDB URLs and payload shapes |
| `movieservice/catalog/query.py` | Natural-language prompt parsing (pure functions) |
| `movieservice/catalog/service.py` | Orchestration, ranking, degradation policy |
| `movieservice/inbound/ratelimit.py` | Sliding-window inbound quota |
| `movieservice/observability.py` | JSON logging with per-request correlation ids |

## Resilience and latency behaviour

**Retries.** Timeouts, connection errors, and `408/425/429/5xx` are retried with
exponential backoff plus jitter, capped at `RETRY_MAX_DELAY`. A `Retry-After`
header always overrides our own backoff guess. `401`/`403`/`404` are *not*
retried — a bad token will not fix itself on the second attempt.

**Circuit breaking.** After `BREAKER_FAILURE_THRESHOLD` consecutive failures the
breaker opens and calls fail fast for `BREAKER_RESET_SECONDS`, then a single probe
decides whether to close. This keeps a TMDB outage from turning into a queue of
slow timeouts holding this service's connection pool open. A `404` is a definitive
answer, not a fault, so it never trips the breaker.

**Connection pooling.** One `httpx.AsyncClient` is created lazily and held for the
life of the process (closed on shutdown via the ASGI lifespan), so requests reuse
warm TLS connections rather than paying a handshake each time. The MCP adapter
builds its service once for the same reason.

**Caching and single-flight.** Responses are cached by path plus normalised query
string. Concurrent misses on the same key share one in-flight request instead of
stampeding the upstream — five simultaneous identical searches produce one TMDB
call.

**Concurrent orchestration.** `get_movie_details` fans out to `/movie`,
`/credits`, `/recommendations`, and `/watch/providers` together, so it costs
roughly one round trip instead of four (~150 ms rather than ~600 ms).

**Graceful degradation.** In that fan-out the detail call is required; cast and
recommendations are enrichment. If enrichment fails the response still returns,
with the missing pieces named in `meta.partial` rather than silently dropped.
Likewise, a genre-lookup outage downgrades search to title matching instead of
failing the request.

## HTTP API

| Route | Purpose |
| --- | --- |
| `GET /` | The demo UI |
| `POST /api/search` | `{"query": "scary movies from 2022"}` → parsed intent + results |
| `GET /api/movies/{id}?region=IN` | Orchestrated detail + cast + similar + where to watch |
| `GET /api/movies/{id}/watch?region=IN` | Just the watch options |
| `GET /health`, `GET /api/health` | Liveness plus breaker state, cache stats, and request metrics — returns `503` when a dependency breaker is open |
| `GET /docs`, `GET /openapi.json` | Generated API documentation |

Every response carries an `x-request-id`, echoing an inbound one when supplied, and
every log line is single-line JSON tagged with the same id. Request and response
bodies are declared as Pydantic models, so the OpenAPI document describes the real
contract rather than an opaque dict.

## MCP tools

| Tool | Description |
| --- | --- |
| `search_movies(query)` | Title, genre, mood, or year search |
| `get_movie_details(movie_id, region)` | Detail + cast + where to watch + similar, fetched concurrently |
| `get_watch_providers(movie_id, region)` | Where a movie streams, rents, or sells |
| `get_recommendations(movie_id)` | Recommendations for a TMDB id |

## Where to watch

Every result in the demo is clickable and opens a panel showing where the movie
can be streamed, rented, or bought, with provider logos grouped by tier. A region
selector sits next to the search box; availability is regional, so the answer
always states which region it describes.

Region is resolved in this order: an explicit `?region=` value, then Vercel's
`x-vercel-ip-country` geo header (so a first-time visitor sees their own
country's services without asking), then `DEFAULT_WATCH_REGION`. Anything that
is not a two-letter code falls back to the default rather than reaching TMDB.

TMDB returns all ~107 regions in a single document, so the cache key is the
movie rather than the movie-and-region: one upstream call answers a viewer in the
US and one in India alike, and it is held for `CACHE_WATCH_TTL_SECONDS` since
availability moves slowly.

This data is sourced from JustWatch, and TMDB's terms require that attribution
wherever it appears — so the API returns an `attribution` field, and both the web
panel and the MCP tool output carry the credit.

## Prerequisites

- Python 3.11+
- A TMDB read access token from [themoviedb.org](https://www.themoviedb.org/settings/api)

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then set `TMDB_TOKEN` in `.env`.

## Run

Web API and demo UI:

```bash
python webapp.py
```

Open [http://localhost:5000](http://localhost:5000), or
[/docs](http://localhost:5000/docs) for the generated API reference. MCP server
over stdio:

```bash
python server.py
```

To connect it to Claude Desktop, point the config at your virtualenv Python:

```json
{
  "mcpServers": {
    "movie-assistant": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/movie-mcp-server/server.py"],
      "env": {
        "TMDB_TOKEN": "your_tmdb_read_api_bearer_token_here"
      }
    }
  }
}
```

## Configuration

Every knob is an environment variable with a working default — see
`movieservice/config.py`.

| Variable | Default | Effect |
| --- | --- | --- |
| `TMDB_TOKEN` | — | Required TMDB read access token |
| `REQUEST_TIMEOUT` / `CONNECT_TIMEOUT` | `8.0` / `3.0` | Per-request latency budget |
| `POOL_MAX_CONNECTIONS` / `POOL_MAX_KEEPALIVE` | `20` / `10` | Connection pool size |
| `RETRY_ATTEMPTS` | `3` | Total attempts, including the first |
| `RETRY_BASE_DELAY` / `RETRY_MAX_DELAY` / `RETRY_JITTER` | `0.2` / `2.0` / `0.25` | Backoff shape |
| `BREAKER_FAILURE_THRESHOLD` / `BREAKER_RESET_SECONDS` | `5` / `20` | Circuit breaker sensitivity |
| `CACHE_TTL_SECONDS` / `CACHE_GENRE_TTL_SECONDS` / `CACHE_WATCH_TTL_SECONDS` / `CACHE_MAX_ENTRIES` | `60` / `3600` / `900` / `512` | Response cache |
| `DEFAULT_WATCH_REGION` | `US` | Fallback region for watch availability |
| `RATE_LIMIT_MAX_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | `30` / `3600` | Inbound quota per client IP |
| `MAX_QUERY_LENGTH` | `140` | Rejects oversized prompts |
| `LOG_LEVEL` | `INFO` | JSON log verbosity |

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

82 tests, no network access — upstream behaviour is simulated with
`httpx.MockTransport`, and backoff sleeps are stubbed, so the suite runs in about
a tenth of a second. Coverage includes the backoff schedule, `Retry-After`
handling, breaker open/half-open/reopen transitions, LRU eviction, single-flight
coalescing, gateway payload mapping, concurrent fan-out timing, degradation on
partial failure, region resolution and normalisation, and both adapters'
contracts.

## Deploy

The HTTP layer is a FastAPI (ASGI) application, so any ASGI host works:

```bash
uvicorn webapp:app --host 0.0.0.0 --port 8000
```

On Vercel it deploys as a single Python Function. Vercel looks for a FastAPI
instance named `app` at a supported entrypoint, and `app.py` re-exports it for
exactly that. `public/static/` holds the browser assets Vercel serves from its
CDN, `.python-version` pins the runtime, and `.vercelignore` keeps local-only
files out of the bundle. Set `TMDB_TOKEN` in the project's environment settings,
then check `/health` on the deployed URL.

**One caveat when running serverless.** The cache, circuit breaker, and rate
limiter are in-process. On a long-lived server that is exactly what you want; on
Vercel each function instance keeps its own copy, so a cache hit depends on
landing on a warm instance and the rate limit is per instance rather than global.
The behaviour is still correct — just less effective than it is when the service
runs as a persistent process. Moving that state to Redis would make it shared,
and the interfaces in `transport/cache.py` and `inbound/ratelimit.py` are shaped
to be swapped without touching callers.

## Demo prompts

- `Dune 2024` — title plus year
- `scary movies from 2022` — mood synonym plus year
- `animated family 2023` — genre synonyms
- `What movies are similar to Inception?` — via the MCP tools
