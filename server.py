import httpx
import os
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.themoviedb.org/3"
REQUEST_TIMEOUT = 15.0

mcp = FastMCP("Movie Assistant")


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


async def tmdb_get(path: str, params: dict[str, str | int] | None = None) -> dict:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(
            f"{BASE_URL}{path}",
            headers=get_headers(),
            params=params,
        )
        response.raise_for_status()
        return response.json()

@mcp.tool()
async def search_movies(query: str) -> str:
    """Search for movies by title or keyword."""
    try:
        data = await tmdb_get("/search/movie", {"page": 1, "query": query})
    except (RuntimeError, httpx.HTTPError) as exc:
        return f"TMDB request failed: {exc}"

    results = data.get("results", [])[:5]
    if not results:
        return "No movies found."

    output = []
    for movie in results:
        output.append(
            f"- {movie['title']} ({movie.get('release_date', 'N/A')[:4]}) "
            f"| Rating: {movie.get('vote_average', 'N/A')} "
            f"| ID: {movie['id']}"
        )
    return "\n".join(output)

@mcp.tool()
async def get_movie_details(movie_id: int) -> str:
    """Get detailed info about a specific movie by its ID."""
    try:
        movie = await tmdb_get(f"/movie/{movie_id}")
    except (RuntimeError, httpx.HTTPError) as exc:
        return f"TMDB request failed: {exc}"

    return (
        f"Title: {movie.get('title')}\n"
        f"Release Date: {movie.get('release_date')}\n"
        f"Rating: {movie.get('vote_average')}/10\n"
        f"Runtime: {movie.get('runtime')} minutes\n"
        f"Genres: {', '.join(g['name'] for g in movie.get('genres', []))}\n"
        f"Overview: {movie.get('overview')}"
    )

@mcp.tool()
async def get_recommendations(movie_id: int) -> str:
    """Get movie recommendations based on a movie ID."""
    try:
        data = await tmdb_get(f"/movie/{movie_id}/recommendations")
    except (RuntimeError, httpx.HTTPError) as exc:
        return f"TMDB request failed: {exc}"

    results = data.get("results", [])[:5]
    if not results:
        return "No recommendations found."

    output = []
    for movie in results:
        output.append(
            f"- {movie['title']} ({movie.get('release_date', 'N/A')[:4]}) "
            f"| Rating: {movie.get('vote_average', 'N/A')}"
        )
    return "\n".join(output)

if __name__ == "__main__":
    mcp.run(transport="stdio")
