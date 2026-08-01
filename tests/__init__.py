"""Test suite for the movie catalog service."""

import logging
import os

# Tests exercise failure paths on purpose. Keep their logs out of the test
# output: this runs before any test module imports the adapters, so it also
# decides the level `configure_logging` picks up.
os.environ.setdefault("TMDB_TOKEN", "test-token")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")

logging.getLogger("movieservice").addHandler(logging.NullHandler())
logging.getLogger("httpx").setLevel(logging.WARNING)
