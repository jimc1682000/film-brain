"""Integration tests for the display band + low-confidence gate on /api/search.

Internal ranking scores are relative (min-max / CE blend) so rank-1 is always
1.0 — the band maps that into user-facing percentages that are never a fake
100%, and the primary-cosine gate flags out-of-domain queries (the
"Michael Jackson → 一戰再戰 100%" confusion).
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app

# Mirror search-config confidence_tiers bands (data/search-config.json).
CONFIDENT_HI = 0.95  # high band ceiling
LOW_HI = 0.42  # low band ceiling (out-of-domain cap)

_EXPAND = {
    "filters": {},
    "hyde_text": "一段假想的劇情大綱",
    "keywords": [],
    "boost_tags": [],
    "stepback_text": "",
}


@pytest.fixture
def client():
    return TestClient(app)


def _candidates(primary_cos: float):
    """Hybrid recall output — top hit carries the given primary cosine."""
    rows = [
        ("f-top", "第一片", 0.03, primary_cos),
        ("f-mid", "第二片", 0.02, primary_cos * 0.9),
        ("f-low", "第三片", 0.01, primary_cos * 0.8),
    ]
    return [
        {
            "film_id": fid,
            "title_zh": zh,
            "title_en": None,
            "poster_url": None,
            "score": cos,
            "tags": [],
            "rrf_score": rrf,
            "primary_cos": cos,
            "sources": ["vector"],
        }
        for fid, zh, rrf, cos in rows
    ]


def _search(client, candidates):
    with (
        patch("backend.routers.search.get_embed_service") as mock_embed,
        patch("backend.routers.search.get_qdrant_client", return_value=MagicMock()),
        patch("backend.routers.search.hybrid_candidates", return_value=candidates),
        patch("backend.routers.search.expand_query", return_value=dict(_EXPAND)),
    ):
        mock_embed.return_value.embed_single.return_value = [0.0] * 1024
        mock_embed.return_value.tag_vector_cache = {}
        r = client.post("/api/search/", json={"query": "test", "use_llm_rerank": False})
    assert r.status_code == 200, r.text
    return r.json()


CONFIDENT_LO = 0.72  # high band floor
LOW_LO = 0.20  # low band floor


def test_confident_query_tops_below_100(client):
    """In-domain query: no low-confidence flag, and rank-1 shows the band cap,
    never a fake 100%."""
    data = _search(client, _candidates(primary_cos=0.6))
    assert data["understanding"]["low_confidence"] is False
    scores = [r["score"] for r in data["results"]]
    assert data["understanding"]["confidence"] == "high"
    assert scores[0] == pytest.approx(CONFIDENT_HI)
    assert all(s < 1.0 for s in scores)
    # Pool min-max stretches the hits across the whole band, so the last shown
    # hit sits at the band floor instead of huddling near the cap.
    assert scores[-1] == pytest.approx(CONFIDENT_LO)


def test_mid_tier_query_caps_below_high(client):
    """Borderline cosine (0.45–0.52) → mid tier, capped below the high band."""
    data = _search(client, _candidates(primary_cos=0.48))
    assert data["understanding"]["confidence"] == "mid"
    assert data["understanding"]["low_confidence"] is False
    scores = [r["score"] for r in data["results"]]
    assert scores[0] == pytest.approx(0.68)  # mid band ceiling
    assert all(s < CONFIDENT_LO for s in scores)


def test_out_of_domain_query_flags_low_confidence_and_caps_score(client):
    """Primary cosine below the calibrated threshold → flag + lower band cap."""
    data = _search(client, _candidates(primary_cos=0.37))
    assert data["understanding"]["low_confidence"] is True
    assert data["understanding"]["confidence"] == "low"
    scores = [r["score"] for r in data["results"]]
    assert scores[0] == pytest.approx(LOW_HI)
    assert all(s <= LOW_HI for s in scores)
    assert scores[-1] == pytest.approx(LOW_LO)


def test_excluded_tag_film_dropped(client):
    """A film carrying a user-excluded tag (gate ✕ → exclude=["恐怖"] → horror)
    is penalised below the display floor and disappears; others remain."""
    cands = _candidates(primary_cos=0.6)
    cands[0]["tags"] = ["horror"]  # the top hit is the excluded direction
    with (
        patch("backend.routers.search.get_embed_service") as mock_embed,
        patch("backend.routers.search.get_qdrant_client", return_value=MagicMock()),
        patch("backend.routers.search.hybrid_candidates", return_value=cands),
        patch("backend.routers.search.expand_query", return_value=dict(_EXPAND)),
    ):
        mock_embed.return_value.embed_single.return_value = [0.0] * 1024
        mock_embed.return_value.tag_vector_cache = {}
        r = client.post(
            "/api/search/",
            json={"query": "test", "use_llm_rerank": False, "exclude": ["恐怖"]},
        )
    assert r.status_code == 200, r.text
    data = r.json()
    ids = [x["film_id"] for x in data["results"]]
    assert "f-top" not in ids  # excluded-tag film penalised below floor → gone
    assert "f-mid" in ids  # a non-excluded film still shows
    assert data["understanding"]["excluded"] == ["恐怖"]


def test_low_confidence_cap_survives_tag_boost(client):
    """A tag-boosted hit on an out-of-domain query must stay inside the low
    band — boost must not paint the guess green again."""
    cands = _candidates(primary_cos=0.37)
    cands[0]["tags"] = ["american"]  # region dim → strong boost weight
    with (
        patch("backend.routers.search.get_embed_service") as mock_embed,
        patch("backend.routers.search.get_qdrant_client", return_value=MagicMock()),
        patch("backend.routers.search.hybrid_candidates", return_value=cands),
        patch(
            "backend.routers.search.expand_query",
            return_value={**_EXPAND, "boost_tags": [["american", 2.0]]},
        ),
    ):
        mock_embed.return_value.embed_single.return_value = [0.0] * 1024
        mock_embed.return_value.tag_vector_cache = {}
        r = client.post("/api/search/", json={"query": "test", "use_llm_rerank": False})
    assert r.status_code == 200, r.text
    scores = [x["score"] for x in r.json()["results"]]
    assert all(s <= LOW_HI for s in scores)


def test_hyde_text_surfaces_in_understanding(client):
    """The HyDE plot (the WHY behind pure-semantic hits) reaches the UI."""
    data = _search(client, _candidates(primary_cos=0.6))
    assert data["understanding"]["hyde_text"] == _EXPAND["hyde_text"]


def test_degraded_expansion_is_not_cached(client):
    """If the LLM expansion degrades (rate-limit/error), the result must NOT be
    cached — else an empty 'AI understanding' gets pinned until restart."""
    from backend.routers import search as _s

    with (
        patch("backend.routers.search.get_embed_service") as mock_embed,
        patch("backend.routers.search.get_qdrant_client", return_value=MagicMock()),
        patch("backend.routers.search.hybrid_candidates", return_value=_candidates(0.6)),
        patch(
            "backend.routers.search.expand_query",
            return_value={**_EXPAND, "_degraded": True},
        ),
    ):
        mock_embed.return_value.embed_single.return_value = [0.0] * 1024
        mock_embed.return_value.tag_vector_cache = {}
        r = client.post("/api/search/", json={"query": "test", "use_llm_rerank": False})
    assert r.status_code == 200, r.text
    assert len(_s._heavy_cache) == 0  # degraded → skipped
