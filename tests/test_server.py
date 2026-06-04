import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("TMDB_TOKEN", "test-token")

import server


class ServerTests(unittest.IsolatedAsyncioTestCase):
    @patch("server.httpx.AsyncClient")
    async def test_search_movies_formats_results(self, async_client_cls):
        response = Mock()
        response.json.return_value = {
            "results": [
                {
                    "id": 11,
                    "release_date": "2024-03-01",
                    "title": "Dune: Part Two",
                    "vote_average": 8.5,
                }
            ]
        }
        response.raise_for_status.return_value = None

        client = AsyncMock()
        client.get.return_value = response
        async_client_cls.return_value.__aenter__.return_value = client

        result = await server.search_movies("dune")

        self.assertIn("Dune: Part Two", result)
        self.assertIn("ID: 11", result)

    @patch("server.httpx.AsyncClient")
    async def test_get_movie_details_formats_payload(self, async_client_cls):
        response = Mock()
        response.json.return_value = {
            "genres": [{"name": "Science Fiction"}],
            "overview": "Paul returns to Arrakis.",
            "release_date": "2024-03-01",
            "runtime": 166,
            "title": "Dune: Part Two",
            "vote_average": 8.5,
        }
        response.raise_for_status.return_value = None

        client = AsyncMock()
        client.get.return_value = response
        async_client_cls.return_value.__aenter__.return_value = client

        result = await server.get_movie_details(11)

        self.assertIn("Title: Dune: Part Two", result)
        self.assertIn("Science Fiction", result)


if __name__ == "__main__":
    unittest.main()
