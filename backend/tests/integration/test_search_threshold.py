"""Integration test for the post-rerank display-score floor on /api/search."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.reranker import get_reranker


class _FakeReranker:
    """Reranker Protocol double — returns a precomputed reordering."""

    def __init__(self, reranked):
        self._reranked = reranked

    def rerank(self, query, candidates):
        return self._reranked


@pytest.fixture
def client():
    return TestClient(app)


def _fake_hits():
    return [
        {
            "film_id": "f-high",
            "title_zh": "高分片",
            "title_en": "High",
            "poster_url": None,
            "score": 0.9,
            "tags": [],
        },
        {
            "film_id": "f-mid",
            "title_zh": "中分片",
            "title_en": "Mid",
            "poster_url": None,
            "score": 0.7,
            "tags": [],
        },
        {
            "film_id": "f-low",
            "title_zh": "低分片",
            "title_en": "Low",
            "poster_url": None,
            "score": 0.62,
            "tags": [],
        },
    ]


def _make_rerank_output(hits, scores):
    """Mimic reranker.rerank_with_cross_encoder output."""
    out = []
    for h, s in zip(hits, scores, strict=False):
        merged = dict(h)
        merged["llm_score"] = s
        merged["ce_logit"] = s
        merged["llm_reason"] = ""
        out.append(merged)
    out.sort(key=lambda x: -x["ce_logit"])
    return out


def _candidates(hits):
    """Hybrid recall output — hits carrying an rrf_score."""
    return [{**h, "rrf_score": 0.03 - i * 0.01} for i, h in enumerate(hits)]


def test_min_display_score_filters_low_post_rerank(client):
    """After rerank produces a normalised score, hits below the floor must disappear."""
    hits = _fake_hits()
    # After min-max normalisation: 1.0 / 0.5 / 0.05 (low gets squashed)
    reranked = _make_rerank_output(hits, [1.0, 0.5, 0.05])

    app.dependency_overrides[get_reranker] = lambda: _FakeReranker(reranked)
    try:
        with (
            patch("backend.routers.search.get_embed_service") as mock_embed,
            patch("backend.routers.search.get_qdrant_client", return_value=MagicMock()),
            patch("backend.routers.search.hybrid_candidates", return_value=_candidates(hits)),
            patch(
                "backend.routers.search.expand_query",
                return_value={"filters": {}, "hyde_text": "", "keywords": []},
            ),
        ):
            mock_embed.return_value.embed_single.return_value = [0.0] * 1024
            mock_embed.return_value.tag_vector_cache = {}

            r = client.post(
                "/api/search/",
                json={"query": "test", "min_display_score": 0.1, "use_llm_rerank": True},
            )
    finally:
        app.dependency_overrides.pop(get_reranker, None)

    assert r.status_code == 200, r.text
    ids = [r["film_id"] for r in r.json()["results"]]
    assert "f-high" in ids
    assert "f-mid" in ids
    assert "f-low" not in ids, "low post-rerank score should be hidden by floor"


def test_min_display_score_default_drops_below_10_percent(client):
    """Without explicit min_display_score, the default 0.1 floor applies."""
    hits = _fake_hits()
    reranked = _make_rerank_output(hits, [1.0, 0.5, 0.05])

    app.dependency_overrides[get_reranker] = lambda: _FakeReranker(reranked)
    try:
        with (
            patch("backend.routers.search.get_embed_service") as mock_embed,
            patch("backend.routers.search.get_qdrant_client", return_value=MagicMock()),
            patch("backend.routers.search.hybrid_candidates", return_value=_candidates(hits)),
            patch(
                "backend.routers.search.expand_query",
                return_value={"filters": {}, "hyde_text": "", "keywords": []},
            ),
        ):
            mock_embed.return_value.embed_single.return_value = [0.0] * 1024
            mock_embed.return_value.tag_vector_cache = {}

            r = client.post("/api/search/", json={"query": "test", "use_llm_rerank": True})
    finally:
        app.dependency_overrides.pop(get_reranker, None)

    assert r.status_code == 200, r.text
    ids = [r["film_id"] for r in r.json()["results"]]
    assert "f-low" not in ids


def test_per_request_min_display_score_tightens_floor(client):
    """A high per-request min_display_score must hide mid-band hits the default
    floor would keep — i.e. the request value is honored, not just the config."""
    hits = _fake_hits()
    # Normalised display scores: high=1.0, mid=0.5, low=0.05.
    reranked = _make_rerank_output(hits, [1.0, 0.5, 0.05])

    app.dependency_overrides[get_reranker] = lambda: _FakeReranker(reranked)
    try:
        with (
            patch("backend.routers.search.get_embed_service") as mock_embed,
            patch("backend.routers.search.get_qdrant_client", return_value=MagicMock()),
            patch("backend.routers.search.hybrid_candidates", return_value=_candidates(hits)),
            patch(
                "backend.routers.search.expand_query",
                return_value={"filters": {}, "hyde_text": "", "keywords": []},
            ),
        ):
            mock_embed.return_value.embed_single.return_value = [0.0] * 1024
            mock_embed.return_value.tag_vector_cache = {}

            r = client.post(
                "/api/search/",
                json={"query": "test", "min_display_score": 0.9, "use_llm_rerank": True},
            )
    finally:
        app.dependency_overrides.pop(get_reranker, None)

    assert r.status_code == 200, r.text
    ids = [r["film_id"] for r in r.json()["results"]]
    assert ids == ["f-high"], "only the 1.0 hit clears a 0.9 per-request floor"
