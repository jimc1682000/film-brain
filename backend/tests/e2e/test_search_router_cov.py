"""Coverage tests for the search HTTP surface + service layer — heavy deps
mocked, DB real.

Mocks the names AS IMPORTED into the service/planner namespaces (expand_query,
hybrid_candidates, get_qdrant_client, get_embed_service). DB stays real
against a seeded temp db so strong-inject, _assemble_response's get_film, and
similar_films SQL run on mock-00x ids.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import backend.routers.search as S
import backend.services.search.planner as SP
import backend.services.search.service as SS
from backend.config import settings
from backend.db import init_db
from backend.main import app
from backend.models import SearchRequest
from backend.services.reranker import get_reranker
from backend.services.search import pin_demo_query
from backend.tests.fixtures.mock_films import fake_embed, seed_mock_db
from backend.vector_store import get_vector_store


class _FakeEmbed:
    tag_vector_cache: dict = {}

    def embed(self, texts):
        return fake_embed(texts)

    def embed_single(self, text):
        return fake_embed([text])[0]


class _FakeReranker:
    """Reranker Protocol double — returns a fixed result (None → minmax fallback)."""

    def __init__(self, result=None):
        self._result = result

    def rerank(self, query, candidates):
        return self._result


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db_path = tmp_path / "search.db"
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    seed_mock_db(conn)
    conn.close()
    monkeypatch.setattr(settings, "db_path", db_path, raising=False)
    return db_path


@pytest.fixture
def base_mocks(monkeypatch):
    """Patch heavy deps to deterministic fakes. expand_query returns a minimal
    non-degraded plan by default; tests override per-case."""
    monkeypatch.setattr(SS, "get_embed_service", lambda: _FakeEmbed())
    monkeypatch.setattr(SS, "get_qdrant_client", lambda: object())

    def _expand(query, timeout=None):
        return {
            "filters": {},
            "boost_tags": [],
            "keywords": [],
            "hyde_text": "",
            "stepback_text": "",
            "award_presence": False,
        }

    monkeypatch.setattr(SP, "expand_query", _expand)
    monkeypatch.setattr(settings, "use_query_expansion", True, raising=False)
    # Default: rerank returns None (→ minmax fallback path), injected via the
    # Reranker Protocol seam (ADR 0021) instead of monkeypatching a name.
    app.dependency_overrides[get_reranker] = lambda: _FakeReranker(None)
    try:
        yield monkeypatch
    finally:
        app.dependency_overrides.pop(get_reranker, None)


def _candidates(film_ids, *, primary_cos=0.6):
    """Build hybrid_candidates-shaped dicts for the given mock film ids.

    rrf_score descends per item so _minmax produces a spread above the display
    floor (equal scores would all map to 0.0 and get filtered)."""
    out = []
    for i, fid in enumerate(film_ids):
        out.append(
            {
                "film_id": fid,
                "title_zh": fid,
                "title_en": fid,
                "tags": [],
                "score": 0.5,
                "rrf_score": 0.05 - i * 0.01,
                "primary_cos": primary_cos,
                "sources": ["vector"],
            }
        )
    return out


@pytest.fixture
def client():
    return TestClient(app)


# ── understand_only gate ─────────────────────────────────────────────────────


def test_understand_only_gate(client, seeded_db, base_mocks):
    base_mocks.setattr(SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-001"]))
    r = client.post("/api/search/", json={"query": "搞笑的電影", "understand_only": True})
    assert r.status_code == 200
    data = r.json()
    assert data["results"] == []
    assert data["total"] == 0
    assert "understanding" in data


# ── normal ranking (rerank off → minmax) ─────────────────────────────────────


def test_normal_search_no_rerank(client, seeded_db, base_mocks):
    base_mocks.setattr(
        SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-001", "mock-002"])
    )
    r = client.post(
        "/api/search/",
        json={"query": "驚悚片", "use_llm_rerank": False, "top_k": 5},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert data["results"][0]["film_id"] in {"mock-001", "mock-002"}
    assert data["understanding"]["confidence"] in {"high", "mid", "low"}


# ── rerank on (returns reranked list with llm_score) ─────────────────────────


def test_search_with_rerank(client, seeded_db, base_mocks):
    base_mocks.setattr(
        SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-001", "mock-002"])
    )

    class _Rr:
        def rerank(self, query, cands):
            for i, c in enumerate(cands):
                c["llm_score"] = 1.0 - i * 0.1
            return cands

    app.dependency_overrides[get_reranker] = _Rr
    try:
        r = client.post(
            "/api/search/",
            json={"query": "驚悚片", "use_llm_rerank": True, "rerank_pool": 20},
        )
    finally:
        app.dependency_overrides[get_reranker] = lambda: _FakeReranker(None)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_search_rerank_returns_none_falls_back(client, seeded_db, base_mocks):
    # rerank requested but CE returns None → minmax fallback still ranks.
    base_mocks.setattr(SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-001"]))
    r = client.post("/api/search/", json={"query": "x", "use_llm_rerank": True})
    assert r.status_code == 200


# ── cache hit path (second identical call) ───────────────────────────────────


def test_cache_hit(client, seeded_db, base_mocks):
    calls = {"n": 0}

    def _hc(*a, **k):
        calls["n"] += 1
        return _candidates(["mock-001"])

    base_mocks.setattr(SS, "hybrid_candidates", _hc)
    body = {"query": "快取測試", "use_llm_rerank": False}
    r1 = client.post("/api/search/", json=body)
    r2 = client.post("/api/search/", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    # second call served from heavy cache → hybrid_candidates not re-invoked
    assert calls["n"] == 1


# ── legacy min_confidence param (removed) ────────────────────────────────────


def test_legacy_min_confidence_still_200(client, seeded_db, base_mocks):
    """Old clients still sending the removed min_confidence knob must get a
    normal 200 (Pydantic ignores extras) — and NOT fork the heavy-cache key:
    the same query with a different value is served from the same cache entry."""
    calls = {"n": 0}

    def _hc(*a, **k):
        calls["n"] += 1
        return _candidates(["mock-001"])

    base_mocks.setattr(SS, "hybrid_candidates", _hc)
    r1 = client.post(
        "/api/search/",
        json={"query": "舊客戶端", "use_llm_rerank": False, "min_confidence": 0.6},
    )
    r2 = client.post(
        "/api/search/",
        json={"query": "舊客戶端", "use_llm_rerank": False, "min_confidence": 0.3},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert calls["n"] == 1  # one cache entry serves both values


# ── exclude path (gate ✕) ────────────────────────────────────────────────────


def test_exclude_penalty(client, seeded_db, base_mocks):
    # mock-001 carries 'comedy'. Exclude 喜劇 → it gets penalized below floor.
    cands = _candidates(["mock-001", "mock-002"])
    cands[0]["tags"] = ["comedy"]
    base_mocks.setattr(SS, "hybrid_candidates", lambda *a, **k: cands)
    r = client.post(
        "/api/search/",
        json={"query": "電影", "use_llm_rerank": False, "exclude": ["喜劇"]},
    )
    assert r.status_code == 200
    data = r.json()
    ids = {res["film_id"] for res in data["results"]}
    assert "mock-001" not in ids  # excluded film dropped below display floor
    assert data["understanding"]["excluded"] == ["喜劇"]


# ── strong-inject path + excluded-intersection skip ──────────────────────────


def test_strong_inject(client, seeded_db, base_mocks):
    # award_presence → inject award tags as strong (weight 1.5). But mock data
    # has no award tags, so use a high-weight dimension filter instead: region.
    def _expand(query, timeout=None):
        return {
            "filters": {"region": ["comedy"]},  # region weight=2.0 ≥ threshold 1.5
            "boost_tags": [],
            "keywords": ["foo"],
            "hyde_text": "a hypothetical plot",
            "stepback_text": "abstract",
            "award_presence": False,
        }

    base_mocks.setattr(SP, "expand_query", _expand)
    # recall returns mock-002 only; mock-001/009 carry 'comedy' → injected.
    base_mocks.setattr(SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-002"]))
    r = client.post("/api/search/", json={"query": "喜劇地區", "use_llm_rerank": False})
    assert r.status_code == 200
    ids = {res["film_id"] for res in r.json()["results"]}
    # comedy films injected as strong → present
    assert ids & {"mock-001", "mock-009"}


def test_strong_inject_skips_excluded(client, seeded_db, base_mocks):
    def _expand(query, timeout=None):
        return {
            "filters": {"region": ["comedy"]},
            "boost_tags": [],
            "keywords": [],
            "hyde_text": "",
            "stepback_text": "",
            "award_presence": False,
        }

    base_mocks.setattr(SP, "expand_query", _expand)
    base_mocks.setattr(SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-002"]))
    # exclude comedy → injected comedy films should be skipped at inject time
    r = client.post(
        "/api/search/",
        json={"query": "喜劇", "use_llm_rerank": False, "exclude": ["喜劇"]},
    )
    assert r.status_code == 200
    ids = {res["film_id"] for res in r.json()["results"]}
    assert "mock-001" not in ids and "mock-009" not in ids


# ── award presence path ──────────────────────────────────────────────────────


def test_award_presence(client, seeded_db, base_mocks, monkeypatch):
    def _expand(query, timeout=None):
        return {
            "filters": {},
            "boost_tags": [("comedy", 0.5)],
            "keywords": [],
            "hyde_text": "",
            "stepback_text": "vague",
            "award_presence": True,
        }

    base_mocks.setattr(SP, "expand_query", _expand)
    # award tag id set — patch to a known tag so _add runs the award branch.
    monkeypatch.setattr(SP, "_get_award_tag_ids", lambda: {"drama"})
    base_mocks.setattr(SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-007"]))
    r = client.post("/api/search/", json={"query": "得獎電影", "use_llm_rerank": False})
    assert r.status_code == 200
    assert r.json()["understanding"]["award_required"] is True


# ── degraded expansion (not cached) ──────────────────────────────────────────


def test_degraded_expansion_not_cached(client, seeded_db, base_mocks):
    def _expand(query, timeout=None):
        return {
            "filters": {},
            "boost_tags": [],
            "keywords": [],
            "hyde_text": "",
            "stepback_text": "",
            "award_presence": False,
            "_degraded": True,
        }

    base_mocks.setattr(SP, "expand_query", _expand)
    calls = {"n": 0}

    def _hc(*a, **k):
        calls["n"] += 1
        return _candidates(["mock-001"])

    base_mocks.setattr(SS, "hybrid_candidates", _hc)
    body = {"query": "降級查詢", "use_llm_rerank": False}
    client.post("/api/search/", json=body)
    client.post("/api/search/", json=body)
    assert calls["n"] == 2  # degraded → not cached → recomputed each time
    # understanding surfaces the degraded flag
    r = client.post("/api/search/", json={"query": "降級查詢2", "use_llm_rerank": False})
    assert r.json()["understanding"]["degraded"] is True


# ── low-confidence tier ──────────────────────────────────────────────────────


def test_low_confidence_tier(client, seeded_db, base_mocks):
    # primary_cos well below the mid tier's min_cos (0.45) -> low confidence tier
    base_mocks.setattr(
        SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-001"], primary_cos=0.1)
    )
    r = client.post("/api/search/", json={"query": "冷門", "use_llm_rerank": False})
    assert r.status_code == 200
    assert r.json()["understanding"]["low_confidence"] is True


# ── empty candidates (no results, no cache) ──────────────────────────────────


def test_empty_candidates(client, seeded_db, base_mocks):
    base_mocks.setattr(SS, "hybrid_candidates", lambda *a, **k: [])
    r = client.post("/api/search/", json={"query": "查無", "use_llm_rerank": False})
    assert r.status_code == 200
    assert r.json()["total"] == 0


# ── dimension_filters supplied directly ──────────────────────────────────────


def test_dimension_filters(client, seeded_db, base_mocks):
    base_mocks.setattr(SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-001"]))
    r = client.post(
        "/api/search/",
        json={
            "query": "x",
            "use_llm_rerank": False,
            "dimension_filters": {"genre": ["comedy"]},
        },
    )
    assert r.status_code == 200


# ── pin_demo_query helper ────────────────────────────────────────────────────


def test_pin_demo_query(seeded_db, base_mocks):
    base_mocks.setattr(SS, "hybrid_candidates", lambda *a, **k: _candidates(["mock-001"]))
    import asyncio

    req = SearchRequest(query="釘選", top_k=10, use_llm_rerank=False)
    asyncio.run(S.semantic_search(req))
    assert pin_demo_query(req) is True
    # pinning a never-seen query returns False (not in cache)
    other = SearchRequest(query="未出現的查詢", use_llm_rerank=False)
    assert pin_demo_query(other) is False


# ── similar_films: precomputed rows ──────────────────────────────────────────


def test_similar_films_precomputed(client, seeded_db):
    # Insert precomputed similar rows for mock-001
    conn = sqlite3.connect(str(seeded_db))
    conn.execute(
        "INSERT INTO similar_films (film_id, similar_film_id, rank, score) VALUES (?,?,?,?)",
        ("mock-001", "mock-009", 1, 0.88),
    )
    conn.commit()
    conn.close()
    r = client.get("/api/search/similar/mock-001", params={"top_k": 5})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["results"][0]["film_id"] == "mock-009"


def test_similar_films_precomputed_skips_missing(client, seeded_db):
    conn = sqlite3.connect(str(seeded_db))
    conn.execute(
        "INSERT INTO similar_films (film_id, similar_film_id, rank, score) VALUES (?,?,?,?)",
        ("mock-002", "ghost-film", 1, 0.5),
    )
    conn.commit()
    conn.close()
    r = client.get("/api/search/similar/mock-002")
    assert r.status_code == 200
    assert r.json()["total"] == 0  # ghost film skipped


# ── similar_films: live cosine fallback ──────────────────────────────────────


def test_similar_films_live_fallback(client, seeded_db, monkeypatch):
    # No precomputed rows → live cosine path. Mock qdrant get_film_vector +
    # inject a fake VectorStore via the Protocol seam (ADR 0021).
    monkeypatch.setattr(SS, "get_qdrant_client", lambda: object())
    monkeypatch.setattr(SS, "get_film_vector", lambda client, fid: fake_embed([fid])[0])

    class _Vs:
        def search_films(self, client, query_vector, top_k=10, dimension_filters=None):
            return [
                {"film_id": "mock-001", "title_zh": "笑園", "tags": ["comedy"], "score": 0.9},
                {"film_id": "mock-003", "title_zh": "雨季", "tags": ["romance"], "score": 0.7},
            ]

    app.dependency_overrides[get_vector_store] = _Vs
    try:
        r = client.get("/api/search/similar/mock-009", params={"top_k": 2})
    finally:
        app.dependency_overrides.pop(get_vector_store, None)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert "mock-009" not in {res["film_id"] for res in data["results"]}


def test_similar_films_no_vector_404(client, seeded_db, monkeypatch):
    monkeypatch.setattr(SS, "get_qdrant_client", lambda: object())
    monkeypatch.setattr(SS, "get_film_vector", lambda client, fid: None)
    r = client.get("/api/search/similar/mock-005")
    assert r.status_code == 404
