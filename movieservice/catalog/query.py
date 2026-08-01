"""Natural-language prompt parsing.

Pure functions over a genre map — no I/O, no globals — so the whole parser can
be exercised without a network stub.
"""

from __future__ import annotations

import re

from ..models import ParsedQuery

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

# Words that describe the *request* rather than the movie being looked for.
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

_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")
_WORD_PATTERN = re.compile(r"[a-z0-9']+")


def normalize(text: str) -> str:
    normalized = text.lower()
    for synonym, canonical in GENRE_SYNONYMS.items():
        normalized = normalized.replace(synonym, canonical)
    return normalized


def extract_search_text(normalized: str, year: int | None, genre_name: str | None) -> str:
    stripped = normalized
    if year is not None:
        stripped = re.sub(rf"\b{year}\b", " ", stripped)
    if genre_name:
        stripped = stripped.replace(genre_name, " ")

    words = _WORD_PATTERN.findall(stripped)
    return " ".join(word for word in words if word not in GENERIC_QUERY_TERMS)


def parse_query(text: str, genres: dict[str, int]) -> ParsedQuery:
    """Split a prompt into a genre filter, a year filter, and title keywords."""
    normalized = normalize(text)
    genre_name = next((name for name in genres if name in normalized), None)

    year_match = _YEAR_PATTERN.search(normalized)
    year = int(year_match.group(0)) if year_match else None

    return ParsedQuery(
        genre_id=genres.get(genre_name) if genre_name else None,
        genre_name=genre_name,
        raw_text=normalized,
        search_text=extract_search_text(normalized, year, genre_name),
        year=year,
    )
