"""Integration tests for AutoTagService with mocked LLM."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.db import get_db, init_db, insert_film, insert_tag
from backend.services.auto_tag import AutoTagService
from backend.tag_registry import TagRegistry


@pytest.fixture
def seeded_db(tmp_path):
    """Create a DB with test film and tags."""
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with patch("backend.db.settings") as mock_settings:
        mock_settings.db_path = db_path
        with get_db(db_path) as conn:
            insert_film(
                conn,
                film_id="test-001",
                title_zh="寄生上流",
                title_en="Parasite",
                description="一個窮苦家庭滲透進一個富裕家庭的故事",
                original_genre="劇情",
            )
            insert_tag(conn, "thriller", "genre", "Thriller", "驚悚")
            insert_tag(conn, "dark-comedy", "genre", "Dark Comedy", "黑色喜劇")
            insert_tag(conn, "class-conflict", "theme", "Class Conflict", "階級衝突")
    return db_path


MOCK_LLM_RESPONSE = json.dumps(
    [
        {
            "tag_id": "thriller",
            "dimension": "genre",
            "confidence": 0.9,
            "reasoning": "Suspenseful plot",
        },
        {
            "tag_id": "dark-comedy",
            "dimension": "genre",
            "confidence": 0.85,
            "reasoning": "Dark humor throughout",
        },
        {
            "tag_id": "class-conflict",
            "dimension": "theme",
            "confidence": 0.95,
            "reasoning": "Core theme is class divide",
        },
    ]
)


class TestAutoTagServiceParsing:
    """Test LLM response parsing without actual API calls."""

    def test_parse_json_response(self):
        skill = AutoTagService.__new__(AutoTagService)
        skill._registry = MagicMock()
        # Realistic registry: resolve each id to its own tag. _parse_response
        # now derives tag_id/dimension/labels from the registry (and dedupes),
        # so the mock must return a distinct entry per id, not one for all.
        skill._registry.get_tag.side_effect = lambda tid: (
            {"tag_id": tid, "dimension": "genre", "labels": {"zh_TW": tid, "en": tid}}
            if tid in {"thriller", "dark-comedy", "class-conflict"}
            else None
        )

        result = skill._parse_response(MOCK_LLM_RESPONSE)
        assert len(result) == 3
        assert result[0].tag_id == "thriller"
        assert result[0].confidence == 0.9

    def test_parse_markdown_wrapped_json(self):
        skill = AutoTagService.__new__(AutoTagService)
        skill._registry = MagicMock()
        skill._registry.get_tag.side_effect = lambda tid: (
            {"tag_id": tid, "dimension": "genre", "labels": {"zh_TW": tid, "en": tid}}
            if tid in {"thriller", "dark-comedy", "class-conflict"}
            else None
        )

        wrapped = f"```json\n{MOCK_LLM_RESPONSE}\n```"
        result = skill._parse_response(wrapped)
        assert len(result) == 3

    def test_parse_invalid_json(self):
        skill = AutoTagService.__new__(AutoTagService)
        skill._registry = MagicMock()
        result = skill._parse_response("not valid json")
        assert result == []

    def test_parse_recovers_swapped_fields(self):
        """Small local models (qwen2.5:1.5b) pick the right tags but write the
        dimension name into `tag_id` and the tag into `dimension`. The parser
        resolves orientation-agnostically against the real registry, so a fully
        inverted response still yields the intended tags."""
        skill = AutoTagService.__new__(AutoTagService)
        skill._registry = TagRegistry()
        inverted = json.dumps(
            [
                {"tag_id": "genre", "dimension": "drama", "confidence": 0.9, "reasoning": "x"},
                {"tag_id": "theme", "dimension": "political", "confidence": 0.7, "reasoning": "y"},
            ]
        )
        result = skill._parse_response(inverted)
        ids = {s.tag_id for s in result}
        assert ids == {"drama", "political"}
        # dimension comes from the registry, never the model's mislabel
        by_id = {s.tag_id: s for s in result}
        assert by_id["drama"].dimension == "genre"
        assert by_id["drama"].label_zh_tw == "劇情"

    def test_validate_suggestions(self):
        skill = AutoTagService.__new__(AutoTagService)
        skill._registry = MagicMock()
        skill._registry.all_tag_ids = {"thriller", "dark-comedy"}

        from backend.models import TagSuggestion

        suggestions = [
            TagSuggestion(tag_id="thriller", dimension="genre", confidence=0.9),
            TagSuggestion(tag_id="invalid-tag", dimension="genre", confidence=0.8),
        ]

        valid = skill._validate_suggestions(suggestions)
        assert len(valid) == 1
        assert valid[0].tag_id == "thriller"
