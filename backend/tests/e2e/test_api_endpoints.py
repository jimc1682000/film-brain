"""E2E tests — Full API round-trip via TestClient.

Runs against the synthetic mock dataset (ADR 0020) seeded into a temp DB, so it
needs no real CATCHPLAY catalog and is deterministic in CI / a fresh clone.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.db import init_db
from backend.main import app
from backend.tests.fixtures.mock_films import seed_mock_db


@pytest.fixture(autouse=True)
def _seed_mock(tmp_path, monkeypatch):
    """Point the API at a freshly-seeded synthetic DB (no real catalog needed)."""
    db = tmp_path / "e2e.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    seed_mock_db(conn)
    conn.close()
    monkeypatch.setattr(settings, "db_path", db, raising=False)


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestFilmsEndpoints:
    def test_list_films(self, client):
        r = client.get("/api/films/")
        assert r.status_code == 200
        data = r.json()
        assert "films" in data
        assert "total" in data
        assert isinstance(data["films"], list)

    def test_list_films_with_search(self, client):
        r = client.get("/api/films/", params={"search": "雨"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1  # mock-003「雨季的告白」

    def test_list_films_pagination(self, client):
        r = client.get("/api/films/", params={"limit": 5, "offset": 0})
        assert r.status_code == 200
        data = r.json()
        assert len(data["films"]) <= 5

    def test_get_film_detail(self, client):
        # First get a film_id from the list
        r = client.get("/api/films/", params={"limit": 1})
        films = r.json()["films"]
        if not films:
            pytest.skip("No films in database")
        film_id = films[0]["film_id"]

        r = client.get(f"/api/films/{film_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["film_id"] == film_id
        assert "title_zh" in data
        assert "tags" in data

    def test_get_film_not_found(self, client):
        r = client.get("/api/films/nonexistent-id")
        assert r.status_code == 404


class TestTagsEndpoints:
    def test_list_tags(self, client):
        r = client.get("/api/tags/")
        assert r.status_code == 200
        data = r.json()
        assert "tags" in data
        assert "total" in data
        assert data["total"] > 0

    def test_list_tags_by_dimension(self, client):
        r = client.get("/api/tags/", params={"dimension": "genre"})
        assert r.status_code == 200
        data = r.json()
        for tag in data["tags"]:
            assert tag["dimension"] == "genre"

    def test_dimensions(self, client):
        r = client.get("/api/tags/dimensions")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert "dimension" in data[0]
        assert "tag_count" in data[0]

    def test_films_by_tag(self, client):
        r = client.get("/api/tags/comedy/films")
        assert r.status_code == 200
        data = r.json()
        assert data["tag_id"] == "comedy"
        assert "films" in data


class TestAutoTagEndpoint:
    # The real-LLM auto-tag path is covered keyless via test_auto_tag_router_cov
    # (mocked service) + test_auto_tag unit; live search via test_qdrant_roundtrip
    # (real Qdrant) + test_search_router_cov. The old skipif(True) e2e stubs that
    # needed an API key / live Qdrant were permanently dead and were removed.
    def test_auto_tag_film_not_found(self, client):
        r = client.post("/api/auto-tag/nonexistent")
        assert r.status_code == 404
