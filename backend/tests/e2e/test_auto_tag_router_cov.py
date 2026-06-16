"""Coverage tests for backend.routers.auto_tag — LLM + embed deps mocked.

DB lever: point settings.db_path at a temp DB seeded with mock films/tags so
the real get_db/get_film/insert_film_tag run against deterministic data.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import backend.routers.auto_tag as AT
from backend.config import settings
from backend.db import init_db
from backend.llm_client import LLMRateLimitError
from backend.main import app
from backend.tests.fixtures.mock_films import seed_mock_db


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db_path = tmp_path / "at.db"
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    seed_mock_db(conn)
    conn.close()
    monkeypatch.setattr(settings, "db_path", db_path, raising=False)
    return db_path


@pytest.fixture
def client():
    return TestClient(app)


class _FakeAutoTagService:
    """Async execute returning a canned AutoTagResponse-shaped dict."""

    def __init__(self, *, raise_rate_limit=False):
        self.raise_rate_limit = raise_rate_limit

    async def execute(self, input_data):
        if self.raise_rate_limit:
            raise LLMRateLimitError("quota exhausted")
        return {
            "film_id": input_data["film"].get("film_id", "x"),
            "title": input_data["film"].get("title_zh", ""),
            "suggestions": [
                {
                    "tag_id": "comedy",
                    "dimension": "genre",
                    "label_zh_tw": "喜劇",
                    "label_en": "Comedy",
                    "confidence": 0.9,
                    "reasoning": "funny",
                }
            ],
            "model_used": "fake-model",
        }


def _patch_service(monkeypatch, svc):
    monkeypatch.setattr(AT, "get_auto_tag_service", lambda: svc)


# ── /{film_id}/context ───────────────────────────────────────────────────────


def test_tagging_context(client, seeded_db):
    r = client.get("/api/auto-tag/mock-001/context")
    assert r.status_code == 200
    data = r.json()
    assert data["film"]["film_id"] == "mock-001"
    assert "taxonomy_context" in data
    assert "system_prompt" in data
    assert data["output_schema"]["type"] == "array"


def test_tagging_context_not_found(client, seeded_db):
    r = client.get("/api/auto-tag/nope/context")
    assert r.status_code == 404


# ── /{film_id}/save ──────────────────────────────────────────────────────────


def test_save_tags_valid_and_invalid(client, seeded_db):
    body = {
        "suggestions": [
            {"tag_id": "comedy", "confidence": 0.8},
            {"tag_id": "not-a-real-tag", "confidence": 0.5},
        ],
        "source": "ai-claude-code",
    }
    r = client.post("/api/auto-tag/mock-001/save", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["saved"] == ["comedy"]
    assert data["invalid"] == ["not-a-real-tag"]
    assert data["total_saved"] == 1


def test_save_tags_film_not_found(client, seeded_db):
    r = client.post(
        "/api/auto-tag/nope/save",
        json={"suggestions": [{"tag_id": "comedy", "confidence": 0.8}]},
    )
    assert r.status_code == 404


# ── /{film_id} (auto_tag_film) ───────────────────────────────────────────────


def test_auto_tag_film_ok(client, seeded_db, monkeypatch):
    _patch_service(monkeypatch, _FakeAutoTagService())
    r = client.post("/api/auto-tag/mock-002")
    assert r.status_code == 200
    assert r.json()["suggestions"][0]["tag_id"] == "comedy"


def test_auto_tag_film_not_found(client, seeded_db, monkeypatch):
    _patch_service(monkeypatch, _FakeAutoTagService())
    r = client.post("/api/auto-tag/nope")
    assert r.status_code == 404


def test_auto_tag_film_rate_limited_503(client, seeded_db, monkeypatch):
    _patch_service(monkeypatch, _FakeAutoTagService(raise_rate_limit=True))
    r = client.post("/api/auto-tag/mock-002")
    assert r.status_code == 503
    assert "quota" in r.json()["detail"]


# ── /preview ─────────────────────────────────────────────────────────────────


def test_preview_no_enrich(client, seeded_db, monkeypatch):
    _patch_service(monkeypatch, _FakeAutoTagService())
    monkeypatch.setattr(settings, "tmdb_api_key", "", raising=False)
    body = {"title_zh": "新片", "title_en": "New Film", "enrich": True}
    r = client.post("/api/auto-tag/preview", json=body)
    assert r.status_code == 200
    assert r.json()["suggestions"][0]["tag_id"] == "comedy"
    assert r.json().get("enriched_film") is None


def test_preview_with_enrich(client, seeded_db, monkeypatch):
    _patch_service(monkeypatch, _FakeAutoTagService())
    monkeypatch.setattr(settings, "tmdb_api_key", "k", raising=False)

    class _FakeEnrich:
        async def execute(self, film):
            return {"tmdb_overview": "an overview", "description": "filled"}

    import backend.services.enrichment as enr

    monkeypatch.setattr(enr, "EnrichService", lambda: _FakeEnrich())
    body = {"title_zh": "新片", "description": None, "enrich": True}
    r = client.post("/api/auto-tag/preview", json=body)
    assert r.status_code == 200
    data = r.json()
    # enriched fields applied (film.description was None → filled)
    assert data["enriched_film"]["tmdb_overview"] == "an overview"


def test_preview_enrich_error_swallowed(client, seeded_db, monkeypatch):
    _patch_service(monkeypatch, _FakeAutoTagService())
    monkeypatch.setattr(settings, "tmdb_api_key", "k", raising=False)

    class _BoomEnrich:
        async def execute(self, film):
            raise RuntimeError("tmdb down")

    import backend.services.enrichment as enr

    monkeypatch.setattr(enr, "EnrichService", lambda: _BoomEnrich())
    r = client.post("/api/auto-tag/preview", json={"title_zh": "X", "enrich": True})
    assert r.status_code == 200  # error swallowed, tagging still runs


def test_preview_rate_limited_503(client, seeded_db, monkeypatch):
    _patch_service(monkeypatch, _FakeAutoTagService(raise_rate_limit=True))
    monkeypatch.setattr(settings, "tmdb_api_key", "", raising=False)
    r = client.post("/api/auto-tag/preview", json={"title_zh": "X", "enrich": False})
    assert r.status_code == 503


# ── /create ──────────────────────────────────────────────────────────────────


def test_create_film_from_preview(client, seeded_db, monkeypatch):
    # avoid network: stub poster lookup + embed
    monkeypatch.setattr(AT, "catchplay_poster", lambda url: None)
    monkeypatch.setattr(AT, "_embed_film", lambda film, tags: True)
    body = {
        "catchplay_url": "https://www.catchplay.com/tw/video/12345678-1234-1234-1234-1234567890ab",
        "title_zh": "全新影片",
        "title_en": "Brand New",
        "poster_url": "https://example.com/p.jpg",
        "tags": [
            {"tag_id": "comedy", "confidence": 0.9},
            {"tag_id": "bogus", "confidence": 0.5},
        ],
    }
    r = client.post("/api/auto-tag/create", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["film_id"] == "12345678-1234-1234-1234-1234567890ab"
    assert data["saved_tags"] == 1  # bogus skipped
    assert data["embedded"] is True


def test_create_film_generates_uuid_when_no_url(client, seeded_db, monkeypatch):
    monkeypatch.setattr(AT, "catchplay_poster", lambda url: None)
    monkeypatch.setattr(AT, "_embed_film", lambda film, tags: False)
    r = client.post(
        "/api/auto-tag/create",
        json={"title_zh": "無網址", "tmdb_poster_url": "https://t/p.jpg"},
    )
    assert r.status_code == 200
    assert len(r.json()["film_id"]) == 36  # uuid4
    assert r.json()["embedded"] is False


def test_create_film_conflict(client, seeded_db, monkeypatch):
    monkeypatch.setattr(AT, "catchplay_poster", lambda url: None)
    monkeypatch.setattr(AT, "_embed_film", lambda film, tags: True)
    r = client.post(
        "/api/auto-tag/create",
        json={
            "catchplay_url": "https://www.catchplay.com/tw/video/mock-001",
            "title_zh": "x",
        },
    )
    # mock-001 is not a UUID so url won't parse → new uuid → succeeds.
    assert r.status_code == 200


def test_create_film_conflict_existing_uuid(client, seeded_db, monkeypatch):
    monkeypatch.setattr(AT, "catchplay_poster", lambda url: None)
    monkeypatch.setattr(AT, "_embed_film", lambda film, tags: True)
    url = "https://www.catchplay.com/tw/video/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    body = {"catchplay_url": url, "title_zh": "first"}
    r1 = client.post("/api/auto-tag/create", json=body)
    assert r1.status_code == 200
    r2 = client.post("/api/auto-tag/create", json=body)
    assert r2.status_code == 409


# ── /{film_id}/accept (legacy) ───────────────────────────────────────────────


def test_accept_tags(client, seeded_db):
    r = client.post(
        "/api/auto-tag/mock-003/accept",
        json={"tag_ids": ["comedy", "drama"]},
    )
    assert r.status_code == 200
    assert r.json()["accepted_count"] == 2


def test_accept_tags_empty(client, seeded_db):
    r = client.post("/api/auto-tag/mock-003/accept", json={"tag_ids": []})
    assert r.status_code == 200
    assert r.json()["status"] == "no tags specified"


def test_accept_tags_film_not_found(client, seeded_db):
    r = client.post("/api/auto-tag/nope/accept", json={"tag_ids": ["comedy"]})
    assert r.status_code == 404


# ── _embed_film best-effort failure path ─────────────────────────────────────


def test_embed_film_returns_false_on_failure(monkeypatch):
    # Force the embed/upsert path to raise → exception swallowed → False.
    import backend.services as svc

    monkeypatch.setattr(
        svc, "get_embed_service", lambda: (_ for _ in ()).throw(RuntimeError("no model"))
    )
    assert AT._embed_film({"film_id": "x", "title_zh": "t"}, []) is False
