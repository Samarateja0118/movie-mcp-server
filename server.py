import httpx
import os
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

TMDB_TOKEN = os.getenv("TMDB_TOKEN")
BASE_URL = "https://api.themoviedb.org/3"
HEADERS = {"Authorization": f"Bearer {TMDB_TOKEN}"}

mcp = FastMCP("Movie Assistant")

@mcp.tool()
async def search_movies(query: str) -> str:
    """Search for movies by title or keyword."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/search/movie",
            headers=HEADERS,
            params={"query": query, "page": 1}
        )
        data = response.json()
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
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/movie/{movie_id}",
            headers=HEADERS
        )
        m = response.json()
        return (
            f"Title: {m.get('title')}\n"
            f"Release Date: {m.get('release_date')}\n"
            f"Rating: {m.get('vote_average')}/10\n"
            f"Runtime: {m.get('runtime')} minutes\n"
            f"Genres: {', '.join(g['name'] for g in m.get('genres', []))}\n"
            f"Overview: {m.get('overview')}"
        )

@mcp.tool()
async def get_recommendations(movie_id: int) -> str:
    """Get movie recommendations based on a movie ID."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/movie/{movie_id}/recommendations",
            headers=HEADERS
        )
        data = response.json()
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