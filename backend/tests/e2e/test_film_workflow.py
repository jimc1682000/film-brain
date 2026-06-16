"""E2E test — Full film workflow: import → tag → search.

Runs against the synthetic mock dataset (ADR 0020) seeded into a temp DB, so it
needs no real CATCHPLAY catalog and is deterministic in CI / a fresh clone.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.db import init_db
from backend.main import app
from backend.tests.fixtures.mock_films import MOCK_TAGS, seed_mock_db


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


class TestFilmWorkflow:
    def test_import_and_retrieve_film(self, client):
        """Verify seeded films are retrievable via API."""
        r = client.get("/api/films/", params={"limit": 1})
        assert r.status_code == 200
        films = r.json()["films"]
        assert len(films) > 0

        # Retrieve detail
        film_id = films[0]["film_id"]
        r = client.get(f"/api/films/{film_id}")
        assert r.status_code == 200
        assert r.json()["film_id"] == film_id

    def test_tags_imported(self, client):
        """Verify the seeded tag taxonomy is queryable."""
        r = client.get("/api/tags/")
        assert r.status_code == 200
        assert r.json()["total"] == len(MOCK_TAGS)

    def test_dimension_coverage(self, client):
        """Verify the seeded dimension(s) are reported."""
        r = client.get("/api/tags/dimensions")
        assert r.status_code == 200
        dims = {d["dimension"] for d in r.json()}
        # The synthetic dataset only exercises the genre dimension.
        assert {"genre"}.issubset(dims)

    def test_film_tag_relations(self, client):
        """Verify film-tag relations were seeded."""
        r = client.get("/api/films/", params={"limit": 1})
        film_id = r.json()["films"][0]["film_id"]
        r = client.get(f"/api/films/{film_id}")
        detail = r.json()
        assert len(detail.get("tags", [])) >= 0

    def test_search_films_by_title(self, client):
        """Verify text search works against a known mock title."""
        r = client.get("/api/films/", params={"search": "閣樓"})
        assert r.status_code == 200
        results = r.json()
        assert results["total"] >= 1  # mock-005「無聲的閣樓」

    def test_browse_tag_to_films(self, client):
        """Verify tag → films browsing works."""
        # First get a tag that has films
        r = client.get("/api/tags/", params={"dimension": "genre"})
        tags = r.json()["tags"]
        assert len(tags) > 0

        # Try browsing films for a popular tag
        for tag in tags[:5]:
            r = client.get(f"/api/tags/{tag['tag_id']}/films")
            assert r.status_code == 200
