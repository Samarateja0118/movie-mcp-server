import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TMDB_TOKEN", "test-token")

import webapp


class WebappTests(unittest.TestCase):
    def setUp(self):
        self.client = webapp.app.test_client()
        webapp._rate_limit_hits.clear()

    @patch("webapp.fetch_genres")
    def test_parse_query_keeps_title_and_year(self, fetch_genres):
        fetch_genres.return_value = {"science fiction": 878}

        parsed = webapp.parse_query("Dune 2024")

        self.assertEqual(parsed["search_text"], "dune")
        self.assertEqual(parsed["year"], 2024)
        self.assertIsNone(parsed["genre_id"])

    @patch("webapp.fetch_genres")
    def test_parse_query_detects_filter_only_requests(self, fetch_genres):
        fetch_genres.return_value = {"horror": 27}

        parsed = webapp.parse_query("scary movies from 2022")

        self.assertEqual(parsed["genre_id"], 27)
        self.assertEqual(parsed["search_text"], "")
        self.assertEqual(parsed["year"], 2022)

    @patch("webapp.search_movies")
    @patch("webapp.parse_query")
    def test_api_search_returns_results(self, parse_query, search_movies):
        parse_query.return_value = {"genre_id": 27, "search_text": "", "year": 2022}
        search_movies.return_value = [
            {
                "id": 1,
                "overview": "",
                "poster": None,
                "rating": 7.5,
                "release_date": "2022",
                "title": "Smile",
            }
        ]

        response = self.client.post("/api/search", json={"query": "scary movies from 2022"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["results"][0]["title"], "Smile")

    def test_api_search_requires_query(self):
        response = self.client.post("/api/search", json={"query": "   "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "query required")

    def test_healthcheck_is_available(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_api_search_rejects_long_queries(self):
        response = self.client.post("/api/search", json={"query": "x" * 500})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "query too long")

    @patch("webapp.fetch_json")
    def test_search_movies_prefers_matching_year_for_title_queries(self, fetch_json):
        fetch_json.return_value = {
            "results": [
                {"id": 1, "poster_path": None, "release_date": "2021-10-01", "title": "Dune", "vote_average": 7.8},
                {"id": 2, "poster_path": None, "release_date": "2024-03-01", "title": "Dune: Part Two", "vote_average": 8.1},
                {"id": 3, "poster_path": None, "release_date": "2024-07-01", "title": "Bambi: A Tale of Life in the Woods", "vote_average": 6.2},
            ]
        }

        results = webapp.search_movies(
            {"genre_id": None, "search_text": "dune", "year": 2024},
            limit=5,
        )

        self.assertEqual([movie["title"] for movie in results], ["Dune: Part Two"])


if __name__ == "__main__":
    unittest.main()
