"""Unit tests for backend/models.py Pydantic model validation."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from backend.models import (
    AutoTagAcceptRequest,
    AutoTagResponse,
    DimensionStats,
    Film,
    FilmDetail,
    FilmListResponse,
    FilmTag,
    SearchRequest,
    Tag,
    TagListResponse,
    TagSuggestion,
)

# ---------------------------------------------------------------------------
# Film model
# ---------------------------------------------------------------------------


def test_film_model_required_fields():
    """Film must be constructible with only the two required fields."""
    film = Film(film_id="test-001", title_zh="測試影片")
    assert film.film_id == "test-001"
    assert film.title_zh == "測試影片"


def test_film_model_optional_defaults():
    """All optional Film fields must default to None."""
    film = Film(film_id="test-001", title_zh="測試影片")
    assert film.title_en is None
    assert film.description is None
    assert film.catchplay_url is None
    assert film.poster_url is None
    assert film.original_genre is None
    assert film.tmdb_id is None
    assert film.tmdb_vote_avg is None


def test_film_model_missing_required_film_id_raises():
    """Film must raise ValidationError when film_id is absent."""
    with pytest.raises(ValidationError) as exc_info:
        Film(title_zh="無 ID 影片")  # pyright: ignore[reportCallIssue]
    errors = exc_info.value.errors()
    error_fields = [e["loc"][0] for e in errors]
    assert "film_id" in error_fields


def test_film_model_missing_required_title_zh_raises():
    """Film must raise ValidationError when title_zh is absent."""
    with pytest.raises(ValidationError) as exc_info:
        Film(film_id="test-001")  # pyright: ignore[reportCallIssue]
    errors = exc_info.value.errors()
    error_fields = [e["loc"][0] for e in errors]
    assert "title_zh" in error_fields


def test_film_model_accepts_all_optional_fields():
    """Film must accept all optional fields when provided."""
    film = Film(
        film_id="test-001",
        title_zh="測試",
        title_en="Test",
        description="A test film",
        catchplay_url="https://catchplay.com/test",
        poster_url="https://example.com/poster.jpg",
        original_genre="劇情",
        tmdb_id=12345,
        tmdb_vote_avg=7.8,
    )
    assert film.tmdb_id == 12345
    assert film.tmdb_vote_avg == pytest.approx(7.8)


# ---------------------------------------------------------------------------
# FilmDetail model
# ---------------------------------------------------------------------------


def test_film_detail_with_tags():
    """FilmDetail must hold a list of FilmTag objects and extended metadata."""
    tag = FilmTag(
        tag_id="comedy",
        dimension="genre",
        label_en="Comedy",
        label_zh_tw="喜劇",
        confidence=0.95,
        source="migrated",
    )
    detail = FilmDetail(
        film_id="test-001",
        title_zh="測試影片",
        tags=[tag],
        tmdb_overview="An overview",
        tmdb_director="Some Director",
    )
    assert len(detail.tags) == 1
    assert detail.tags[0].tag_id == "comedy"
    assert detail.tmdb_overview == "An overview"
    assert detail.tmdb_director == "Some Director"


def test_film_detail_tags_default_empty():
    """FilmDetail.tags must default to an empty list when not provided."""
    detail = FilmDetail(film_id="test-001", title_zh="測試影片")
    assert detail.tags == []


def test_film_detail_extended_fields_default_none():
    """FilmDetail extended fields must default to None."""
    detail = FilmDetail(film_id="test-001", title_zh="測試影片")
    assert detail.description_raw is None
    assert detail.tmdb_overview is None
    assert detail.tmdb_genres is None
    assert detail.tmdb_keywords is None
    assert detail.tmdb_cast is None
    assert detail.tmdb_director is None


# ---------------------------------------------------------------------------
# TagSuggestion model – confidence bounds
# ---------------------------------------------------------------------------


def test_tag_suggestion_confidence_bounds_valid():
    """TagSuggestion must accept confidence values at the boundaries 0.0 and 1.0."""
    for boundary in (0.0, 0.5, 1.0):
        suggestion = TagSuggestion(
            tag_id="comedy",
            dimension="genre",
            confidence=boundary,
        )
        assert suggestion.confidence == pytest.approx(boundary)


def test_tag_suggestion_confidence_below_zero_raises():
    """TagSuggestion must raise ValidationError when confidence < 0.0."""
    with pytest.raises(ValidationError) as exc_info:
        TagSuggestion(tag_id="comedy", dimension="genre", confidence=-0.1)
    errors = exc_info.value.errors()
    error_fields = [e["loc"][0] for e in errors]
    assert "confidence" in error_fields


def test_tag_suggestion_confidence_above_one_raises():
    """TagSuggestion must raise ValidationError when confidence > 1.0."""
    with pytest.raises(ValidationError) as exc_info:
        TagSuggestion(tag_id="comedy", dimension="genre", confidence=1.1)
    errors = exc_info.value.errors()
    error_fields = [e["loc"][0] for e in errors]
    assert "confidence" in error_fields


def test_tag_suggestion_optional_fields_default_empty():
    """TagSuggestion optional string fields must default to empty string."""
    suggestion = TagSuggestion(tag_id="drama", dimension="genre", confidence=0.8)
    assert suggestion.label_zh_tw == ""
    assert suggestion.label_en == ""
    assert suggestion.reasoning == ""


# ---------------------------------------------------------------------------
# AutoTagResponse model
# ---------------------------------------------------------------------------


def test_auto_tag_response_timestamp():
    """AutoTagResponse must set timestamp automatically via default_factory."""
    before = datetime.now()
    response = AutoTagResponse(
        film_id="test-001",
        title="測試影片",
        suggestions=[],
        model_used="claude-sonnet-4-20250514",
    )
    after = datetime.now()

    assert isinstance(response.timestamp, datetime)
    assert before <= response.timestamp <= after


def test_auto_tag_response_escalated_defaults_false():
    """AutoTagResponse.escalated must default to False."""
    response = AutoTagResponse(
        film_id="test-001",
        title="測試影片",
        suggestions=[],
        model_used="claude-sonnet-4-20250514",
    )
    assert response.escalated is False


def test_auto_tag_response_with_suggestions():
    """AutoTagResponse must hold and preserve a list of TagSuggestion objects."""
    suggestions = [
        TagSuggestion(tag_id="comedy", dimension="genre", confidence=0.9, reasoning="funny"),
        TagSuggestion(tag_id="tearjerker", dimension="emotion", confidence=0.7),
    ]
    response = AutoTagResponse(
        film_id="test-001",
        title="測試影片",
        suggestions=suggestions,
        model_used="claude-sonnet-4-20250514",
        escalated=True,
    )
    assert len(response.suggestions) == 2
    assert response.suggestions[0].tag_id == "comedy"
    assert response.escalated is True


# ---------------------------------------------------------------------------
# SearchRequest model
# ---------------------------------------------------------------------------


def test_search_request_defaults():
    """SearchRequest must apply default values for top_k and min_confidence."""
    req = SearchRequest(query="romantic comedy")
    assert req.query == "romantic comedy"
    assert req.top_k == 10
    assert req.min_confidence == pytest.approx(0.6)
    assert req.dimension_filters is None


def test_search_request_accepts_custom_values():
    """SearchRequest must accept overridden top_k, min_confidence, and dimension_filters."""
    req = SearchRequest(
        query="thriller",
        top_k=5,
        min_confidence=0.8,
        dimension_filters={"genre": ["crime", "horror"]},
    )
    assert req.top_k == 5
    assert req.min_confidence == pytest.approx(0.8)
    assert req.dimension_filters == {"genre": ["crime", "horror"]}


def test_search_request_missing_query_raises():
    """SearchRequest must raise ValidationError when query is absent."""
    with pytest.raises(ValidationError) as exc_info:
        SearchRequest()  # pyright: ignore[reportCallIssue]
    errors = exc_info.value.errors()
    error_fields = [e["loc"][0] for e in errors]
    assert "query" in error_fields


# ---------------------------------------------------------------------------
# Tag model
# ---------------------------------------------------------------------------


def test_tag_model_defaults():
    """Tag source and status must default to 'migrated' and 'active'."""
    tag = Tag(tag_id="comedy", dimension="genre", label_en="Comedy", label_zh_tw="喜劇")
    assert tag.source == "migrated"
    assert tag.status == "active"
    assert tag.label_in_id is None


def test_tag_model_required_fields():
    """Tag must raise ValidationError when required fields are missing."""
    with pytest.raises(ValidationError):
        Tag(tag_id="comedy")  # pyright: ignore[reportCallIssue]


# ---------------------------------------------------------------------------
# FilmTag model
# ---------------------------------------------------------------------------


def test_film_tag_award_fields_default_none():
    """FilmTag award_year and award_result must default to None."""
    ft = FilmTag(
        tag_id="oscar-best-picture",
        dimension="award",
        label_en="Oscar - Best Picture",
        label_zh_tw="奧斯卡最佳影片",
        confidence=1.0,
        source="award_tracker",
    )
    assert ft.award_year is None
    assert ft.award_result is None


def test_film_tag_with_award_metadata():
    """FilmTag must accept award metadata when provided."""
    ft = FilmTag(
        tag_id="oscar-best-picture",
        dimension="award",
        label_en="Oscar - Best Picture",
        label_zh_tw="奧斯卡最佳影片",
        confidence=1.0,
        source="award_tracker",
        award_year=2024,
        award_result="won",
    )
    assert ft.award_year == 2024
    assert ft.award_result == "won"


# ---------------------------------------------------------------------------
# List response models
# ---------------------------------------------------------------------------


def test_film_list_response_total():
    """FilmListResponse.total must reflect the provided count independently of list length."""
    films = [Film(film_id="f1", title_zh="影片一"), Film(film_id="f2", title_zh="影片二")]
    resp = FilmListResponse(films=films, total=2)
    assert resp.total == 2
    assert len(resp.films) == 2


def test_tag_list_response():
    """TagListResponse must hold tags and a total count."""
    tags = [Tag(tag_id="comedy", dimension="genre", label_en="Comedy", label_zh_tw="喜劇")]
    resp = TagListResponse(tags=tags, total=1)
    assert resp.total == 1
    assert resp.tags[0].tag_id == "comedy"


# ---------------------------------------------------------------------------
# DimensionStats model
# ---------------------------------------------------------------------------


def test_dimension_stats_model():
    """DimensionStats must hold a dimension name and a tag count."""
    stats = DimensionStats(dimension="genre", tag_count=17)
    assert stats.dimension == "genre"
    assert stats.tag_count == 17


# ---------------------------------------------------------------------------
# AutoTagAcceptRequest model
# ---------------------------------------------------------------------------


def test_auto_tag_accept_request_none_means_accept_all():
    """AutoTagAcceptRequest.tag_ids should default to None to indicate accept-all."""
    req = AutoTagAcceptRequest()
    assert req.tag_ids is None


def test_auto_tag_accept_request_with_explicit_ids():
    """AutoTagAcceptRequest must accept a list of tag IDs."""
    req = AutoTagAcceptRequest(tag_ids=["comedy", "drama"])
    assert req.tag_ids == ["comedy", "drama"]
