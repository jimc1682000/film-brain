"""Unit tests for backend/tmdb_lookup.py year/similarity guards."""

from unittest.mock import patch

import pytest

from backend import tmdb_lookup
from backend.tmdb_lookup import MIN_TITLE_SIMILARITY, search_tmdb


def _patch_settings(monkeypatch):
    monkeypatch.setattr(tmdb_lookup.settings, "tmdb_api_key", "dummy", raising=False)


def _mock_search(items):
    class FakeResp:
        status_code = 200

        def json(self):
            return {"results": items}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            return FakeResp()

    return patch.object(tmdb_lookup.httpx, "Client", return_value=FakeClient())


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    _patch_settings(monkeypatch)


def test_search_rejects_release_year_mismatch():
    """Marching Boys (2025) query shouldn't accept ICHU 偶像進行曲 (2021)."""
    anime = {
        "media_type": "movie",
        "id": 112667,
        "title": "ICHU 偶像進行曲",
        "original_title": "アイ★チュウ",
        "release_date": "2021-01-15",
        "poster_path": "/anime.jpg",
        "overview": "anime",
        "vote_average": 6.0,
    }
    real_film = {
        "media_type": "movie",
        "id": 1222574,
        "title": "進行曲",
        "original_title": "Marching Boys",
        "release_date": "2025-03-21",
        "poster_path": "/real.jpg",
        "overview": "...",
        "vote_average": 7.0,
    }
    with _mock_search([anime, real_film]):
        result = search_tmdb("進行曲", release_year=2025)
    assert result is not None
    assert result["tmdb_id"] == 1222574, "anime should be filtered out by year window"


def test_search_skips_low_similarity_even_if_first_in_popularity():
    weak = {
        "media_type": "movie",
        "id": 999,
        "title": "Unrelated Movie",
        "original_title": "Unrelated Movie",
        "release_date": "2024-01-01",
        "poster_path": "/w.jpg",
        "overview": "",
        "vote_average": 5.0,
    }
    strong = {
        "media_type": "movie",
        "id": 1000,
        "title": "Parasite",
        "original_title": "Parasite",
        "release_date": "2019-05-30",
        "poster_path": "/p.jpg",
        "overview": "",
        "vote_average": 8.5,
    }
    with _mock_search([weak, strong]):
        result = search_tmdb("Parasite", min_similarity=MIN_TITLE_SIMILARITY)
    assert result is not None
    assert result["tmdb_id"] == 1000


def test_search_returns_none_when_all_candidates_filtered():
    item = {
        "media_type": "movie",
        "id": 1,
        "title": "Something Else",
        "original_title": "Something Else",
        "release_date": "2024-01-01",
        "poster_path": "",
        "overview": "",
        "vote_average": 5.0,
    }
    with _mock_search([item]):
        result = search_tmdb("進行曲", release_year=2025, min_similarity=0.7)
    assert result is None
