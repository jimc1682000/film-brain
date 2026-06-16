"""Integration tests for EnrichService with mocked TMDb API."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.enrichment import EnrichService

MOCK_SEARCH_RESPONSE = {
    "results": [
        {
            "id": 496243,
            "title": "Parasite",
            "release_date": "2019-05-30",
            "media_type": "movie",
        }
    ]
}

MOCK_DETAIL_RESPONSE = {
    "id": 496243,
    "title": "Parasite",
    "overview": "A poor family schemes to become employed by a wealthy family.",
    "vote_average": 8.5,
    "genres": [{"id": 35, "name": "Comedy"}, {"id": 18, "name": "Drama"}],
    "keywords": {"keywords": [{"id": 1, "name": "class struggle"}]},
    "credits": {
        "cast": [
            {"name": "Song Kang-ho", "character": "Ki-taek"},
            {"name": "Lee Sun-kyun", "character": "Park Dong-ik"},
        ],
        "crew": [
            {"name": "Bong Joon-ho", "job": "Director"},
        ],
    },
}


@pytest.mark.asyncio
async def test_enrich_skill_extracts_data():
    """Test that EnrichService correctly parses TMDb response."""
    skill = EnrichService()

    mock_resp_search = MagicMock()
    mock_resp_search.json.return_value = MOCK_SEARCH_RESPONSE
    mock_resp_search.raise_for_status = MagicMock()

    mock_resp_detail = MagicMock()
    mock_resp_detail.json.return_value = MOCK_DETAIL_RESPONSE
    mock_resp_detail.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[mock_resp_search, mock_resp_detail])

    with patch("backend.services.enrichment.httpx.AsyncClient") as MockAsyncClient:
        MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await skill.execute({"title_en": "Parasite"})

    assert result["tmdb_id"] == 496243
    assert result["tmdb_director"] == "Bong Joon-ho"
    genres = json.loads(result["tmdb_genres"])
    assert "Comedy" in genres
    cast = json.loads(result["tmdb_cast"])
    assert "Song Kang-ho" in cast


@pytest.mark.asyncio
async def test_enrich_skill_no_title():
    skill = EnrichService()
    result = await skill.execute({})
    assert "error" in result
