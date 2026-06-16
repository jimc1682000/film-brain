"""Unit tests for the neutral seed loader (scripts/seed_from_file.py).

parse_film is pure (no IO) so we test it directly. A sync-gate test keeps
data/films.seed.json and the MOCK_FILMS fixture from drifting — they are the
same knowledge (the mock dataset), two representations.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.tests.fixtures.mock_films import MOCK_FILMS
from scripts.seed_from_file import parse_film, primary_title

_VALID = {"comedy", "drama", "sci-fi", "romance"}


def test_primary_title_locale_order():
    assert primary_title({"zh": "中", "en": "EN"}) == "中"  # zh wins
    assert primary_title({"en": "EN", "ja": "JA"}) == "EN"  # en next
    assert primary_title({"ja": "JA"}) == "JA"  # fallback first


def test_parse_film_string_tags_default_confidence():
    out = parse_film({"id": "f1", "titles": {"zh": "片"}, "tags": ["comedy"]}, _VALID)
    assert out["tags"] == [("comedy", 1.0)]
    assert out["title_zh"] == "片" and out["title_en"] is None


def test_parse_film_object_tags_confidence():
    out = parse_film(
        {"id": "f1", "titles": {"zh": "片"}, "tags": [{"tag_id": "drama", "confidence": 0.8}]},
        _VALID,
    )
    assert out["tags"] == [("drama", 0.8)]


def test_parse_film_drops_unknown_tags():
    out = parse_film({"id": "f1", "titles": {"zh": "片"}, "tags": ["comedy", "not-a-tag"]}, _VALID)
    assert out["tags"] == [("comedy", 1.0)]
    assert out["dropped_tags"] == ["not-a-tag"]


def test_parse_film_no_tags():
    out = parse_film({"id": "f1", "titles": {"en": "X"}}, _VALID)
    assert out["tags"] == [] and out["dropped_tags"] == []
    assert out["title_zh"] == "X"  # falls back to en when no zh


def test_parse_film_optional_fields_mapped():
    out = parse_film(
        {
            "id": "f1",
            "titles": {"zh": "片", "en": "Film"},
            "description": "desc",
            "poster": "data:image/svg+xml,x",
            "url": "https://example.com/f1",
            "year": 2024,
            "country": ["US", "KR"],
            "director": "D",
            "cast": "C",
            "tags": [],
        },
        _VALID,
    )
    assert out["release_year"] == 2024
    assert out["country_codes"] == "US,KR"  # ISO list -> CSV
    assert out["tmdb_director"] == "D" and out["tmdb_cast"] == "C"
    assert out["catchplay_url"] == "https://example.com/f1"
    assert out["poster_url"] == "data:image/svg+xml,x"


def test_seed_file_in_sync_with_fixture():
    """data/films.seed.json must stay aligned with MOCK_FILMS (same mock knowledge)."""
    doc = json.loads(Path("data/films.seed.json").read_text(encoding="utf-8"))
    assert doc["version"] == 1
    by_id = {f["id"]: f for f in doc["films"]}
    assert set(by_id) == {f["film_id"] for f in MOCK_FILMS}
    for mf in MOCK_FILMS:
        sf = by_id[mf["film_id"]]
        assert sf["titles"]["zh"] == mf["title_zh"]
        assert sf["description"] == mf["description"]
        assert sf["tags"] == mf["tags"]
