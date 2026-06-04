from __future__ import annotations

import os
import re
import time
from collections import defaultdict, deque
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w342"
MAX_QUERY_LENGTH = int(os.getenv("MAX_QUERY_LENGTH", "140"))
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "30"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "3600"))
REQUEST_TIMEOUT = 15

GENRE_SYNONYMS = {
    "animated": "animation",
    "cartoon": "animation",
    "funny": "comedy",
    "kids": "family",
    "musical": "music",
    "romantic": "romance",
    "scary": "horror",
    "sci-fi": "science fiction",
    "scifi": "science fiction",
    "superhero": "action",
    "suspense": "thriller",
}

GENERIC_QUERY_TERMS = {
    "a",
    "an",
    "best",
    "film",
    "films",
    "find",
    "for",
    "from",
    "get",
    "give",
    "latest",
    "me",
    "movie",
    "movies",
    "new",
    "recommend",
    "recommendations",
    "released",
    "show",
    "something",
    "the",
    "top",
    "watch",
    "with",
}

app = Flask(__name__, static_folder="public/static", template_folder="templates")

_genres_map: dict[str, int] | None = None
_rate_limit_hits: defaultdict[str, deque[float]] = defaultdict(deque)


def get_tmdb_token() -> str:
    token = os.getenv("TMDB_TOKEN")
    if not token:
        raise RuntimeError(
            "TMDB_TOKEN not set. Copy .env.example to .env and add your TMDB "
            "read access token."
        )
    return token


def get_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_tmdb_token()}"}


def fetch_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{BASE_URL}{path}",
        headers=get_headers(),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def fetch_genres() -> dict[str, int]:
    global _genres_map
    if _genres_map is not None:
        return _genres_map

    data = fetch_json("/genre/movie/list", {"language": "en-US"})
    _genres_map = {genre["name"].lower(): genre["id"] for genre in data.get("genres", [])}
    return _genres_map


def normalize_query(query: str) -> str:
    normalized = query.lower()
    for synonym, real_name in GENRE_SYNONYMS.items():
        normalized = normalized.replace(synonym, real_name)
    return normalized


def extract_search_text(normalized_query: str, year: int | None, genre_name: str | None) -> str:
    stripped = normalized_query
    if year is not None:
        stripped = re.sub(rf"\b{year}\b", " ", stripped)
    if genre_name:
        stripped = stripped.replace(genre_name, " ")

    words = re.findall(r"[a-z0-9']+", stripped)
    meaningful_words = [word for word in words if word not in GENERIC_QUERY_TERMS]
    return " ".join(meaningful_words)


def parse_query(query: str) -> dict[str, Any]:
    normalized_query = normalize_query(query)
    genres = fetch_genres()

    genre_name = next((name for name in genres if name in normalized_query), None)
    year_match = re.search(r"\b(19|20)\d{2}\b", normalized_query)
    year = int(year_match.group(0)) if year_match else None
    search_text = extract_search_text(normalized_query, year, genre_name)

    return {
        "genre_id": genres.get(genre_name) if genre_name else None,
        "genre_name": genre_name,
        "raw_text": normalized_query,
        "search_text": search_text,
        "year": year,
    }


def tmdb_image(path: str | None) -> str | None:
    return f"{IMAGE_BASE}{path}" if path else None


def filter_title_matches(results: list[dict[str, Any]], search_text: str) -> list[dict[str, Any]]:
    normalized_search = search_text.strip()
    if not normalized_search:
        return results

    title_matches = [
        movie for movie in results if normalized_search in normalize_query(movie.get("title", ""))
    ]
    return title_matches or results


def get_client_ip() -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def check_rate_limit(client_ip: str, now: float | None = None) -> tuple[bool, int]:
    current_time = time.time() if now is None else now
    window_start = current_time - RATE_LIMIT_WINDOW_SECONDS
    hits = _rate_limit_hits[client_ip]

    while hits and hits[0] <= window_start:
        hits.popleft()

    if len(hits) >= RATE_LIMIT_MAX_REQUESTS:
        retry_after = max(1, int(RATE_LIMIT_WINDOW_SECONDS - (current_time - hits[0])))
        return False, retry_after

    hits.append(current_time)
    return True, 0


def search_movies(parsed: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    if parsed["search_text"]:
        params: dict[str, Any] = {
            "language": "en-US",
            "page": 1,
            "query": parsed["search_text"],
        }
        if parsed["year"]:
            params["year"] = parsed["year"]
        data = fetch_json("/search/movie", params)
    else:
        params = {"language": "en-US", "page": 1, "sort_by": "popularity.desc"}
        if parsed["genre_id"]:
            params["with_genres"] = parsed["genre_id"]
        if parsed["year"]:
            params["primary_release_year"] = parsed["year"]
        data = fetch_json("/discover/movie", params)

    source_results = data.get("results", [])
    if parsed["search_text"]:
        source_results = filter_title_matches(source_results, parsed["search_text"])

    if parsed["search_text"] and parsed["year"]:
        year_matches = [
            movie
            for movie in source_results
            if movie.get("release_date", "").startswith(str(parsed["year"]))
        ]
        if year_matches:
            source_results = year_matches

    results = []
    for movie in source_results[:limit]:
        results.append(
            {
                "id": movie.get("id"),
                "overview": movie.get("overview", ""),
                "poster": tmdb_image(movie.get("poster_path")),
                "rating": round(movie.get("vote_average", 0), 1),
                "release_date": movie.get("release_date", "")[:4],
                "title": movie.get("title"),
            }
        )
    return results


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/health")
@app.route("/api/health")
def healthcheck():
    return jsonify({"ok": True, "service": "movie-finder-demo"})


@app.route("/api/search", methods=["POST"])
def api_search():
    payload = request.get_json(silent=True) or {}
    query = payload.get("query", "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400
    if len(query) > MAX_QUERY_LENGTH:
        return (
            jsonify(
                {
                    "error": "query too long",
                    "detail": f"Keep your query under {MAX_QUERY_LENGTH} characters.",
                }
            ),
            400,
        )

    is_allowed, retry_after = check_rate_limit(get_client_ip())
    if not is_allowed:
        response = jsonify(
            {
                "error": "rate limit exceeded",
                "detail": "This live demo is temporarily busy. Please try again a little later.",
            }
        )
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    try:
        parsed = parse_query(query)
        movies = search_movies(parsed)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except requests.RequestException as exc:
        return jsonify({"error": "TMDB request failed", "detail": str(exc)}), 502

    return jsonify({"parsed": parsed, "query": query, "results": movies})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
