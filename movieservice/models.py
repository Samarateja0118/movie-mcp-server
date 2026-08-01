"""Structured response models.

Raw TMDB payloads are parsed into these models at the gateway boundary, so every
layer above works with validated, typed data instead of arbitrary dicts. If TMDB
changes a field, exactly one ``from_tmdb`` classmethod changes with it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


def _year_of(release_date: str | None) -> str:
    return (release_date or "")[:4]


def _poster_url(path: str | None, image_base: str) -> str | None:
    return f"{image_base}{path}" if path else None


class MovieSummary(BaseModel):
    """A movie as it appears in a list (search, discover, recommendations)."""

    id: int
    title: str
    overview: str = ""
    poster: str | None = None
    rating: float = 0.0
    release_date: str = ""
    popularity: float = 0.0

    @classmethod
    def from_tmdb(cls, payload: dict[str, Any], *, image_base: str) -> "MovieSummary":
        return cls(
            id=int(payload.get("id", 0)),
            title=payload.get("title") or payload.get("name") or "Untitled",
            overview=payload.get("overview") or "",
            poster=_poster_url(payload.get("poster_path"), image_base),
            rating=round(float(payload.get("vote_average") or 0.0), 1),
            release_date=_year_of(payload.get("release_date")),
            popularity=float(payload.get("popularity") or 0.0),
        )


class CastMember(BaseModel):
    name: str
    character: str = ""
    order: int = 0

    @classmethod
    def from_tmdb(cls, payload: dict[str, Any]) -> "CastMember":
        return cls(
            name=payload.get("name") or "Unknown",
            character=payload.get("character") or "",
            order=int(payload.get("order") or 0),
        )


class MovieDetail(BaseModel):
    """The full record for one movie."""

    id: int
    title: str
    overview: str = ""
    poster: str | None = None
    rating: float = 0.0
    release_date: str = ""
    runtime_minutes: int | None = None
    genres: list[str] = Field(default_factory=list)
    tagline: str = ""
    homepage: str | None = None

    @classmethod
    def from_tmdb(cls, payload: dict[str, Any], *, image_base: str) -> "MovieDetail":
        return cls(
            id=int(payload.get("id", 0)),
            title=payload.get("title") or "Untitled",
            overview=payload.get("overview") or "",
            poster=_poster_url(payload.get("poster_path"), image_base),
            rating=round(float(payload.get("vote_average") or 0.0), 1),
            release_date=payload.get("release_date") or "",
            runtime_minutes=payload.get("runtime"),
            genres=[g["name"] for g in payload.get("genres", []) if g.get("name")],
            tagline=payload.get("tagline") or "",
            homepage=payload.get("homepage") or None,
        )


class ParsedQuery(BaseModel):
    """What the natural-language parser understood from a prompt.

    Field names are part of the public API contract consumed by the browser
    client, so they stay stable even as parsing improves.
    """

    genre_id: int | None = None
    genre_name: str | None = None
    raw_text: str = ""
    search_text: str = ""
    year: int | None = None

    @property
    def is_filter_only(self) -> bool:
        """True when the prompt described a category rather than a title."""
        return not self.search_text


class SearchResponse(BaseModel):
    query: str
    parsed: ParsedQuery
    results: list[MovieSummary]
    strategy: str = "search"
    elapsed_ms: int = 0


class MovieOverview(BaseModel):
    """A movie plus its cast and neighbours, fetched as one orchestrated unit."""

    detail: MovieDetail
    cast: list[CastMember] = Field(default_factory=list)
    similar: list[MovieSummary] = Field(default_factory=list)
    partial: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0
