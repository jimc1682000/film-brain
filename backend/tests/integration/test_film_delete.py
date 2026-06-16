"""Integration tests for DELETE /api/films/{film_id} cascade behaviour."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.db import (
    get_db,
    get_film,
    init_db,
    insert_film,
    insert_film_tag,
    insert_tag,
    insert_tag_review,
)
from backend.main import app


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    monkeypatch.setattr("backend.config.settings.db_path", db_path)
    monkeypatch.setattr("backend.db.settings.db_path", db_path)

    with get_db(db_path) as conn:
        insert_tag(conn, "thriller", "genre", "Thriller", "驚悚")
        insert_film(conn, film_id="f-1", title_zh="片", title_en="Film", description="d")
        insert_film(conn, film_id="f-other", title_zh="他", title_en="Other", description="d")
        insert_film_tag(conn, "f-1", "thriller", confidence=0.8, source="ai")
        insert_film_tag(conn, "f-other", "thriller", confidence=0.7, source="ai")
        insert_tag_review(conn, "f-1", "thriller", "approved", "editor")
        # Award nominee pointing at f-1
        conn.execute(
            "INSERT INTO award_nominees (org_id, tag_id, year, category, "
            "  film_title_primary, result, matched_film_id, match_score) "
            "VALUES ('oscars', 'oscars-best-picture-nominee', 2025, 'Best Picture', "
            "  '片', 'nominated', 'f-1', 1.0)"
        )

    return TestClient(app), db_path


def test_delete_film_cascades_tags_reviews_and_unlinks_nominees(client_db):
    client, db_path = client_db

    # Patch the Qdrant client used by the delete endpoint so the test does not
    # require a live vector store. Returning a no-op delete is fine — film_id
    # presence in SQL is the source of truth.
    with patch("backend.routers.films.get_qdrant_client") as mock_q:
        mock_q.return_value.delete.return_value = None
        r = client.delete("/api/films/f-1")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["film_deleted"] == 1
    assert body["tags_deleted"] == 1
    assert body["reviews_deleted"] == 1
    assert body["nominees_unlinked"] == 1
    assert body["vector_deleted"] is True

    with get_db(db_path) as conn:
        assert get_film(conn, "f-1") is None
        # Other film untouched
        assert get_film(conn, "f-other") is not None
        # Nominee row preserved but unlinked
        row = conn.execute(
            "SELECT matched_film_id, match_score FROM award_nominees WHERE film_title_primary='片'"
        ).fetchone()
        assert row["matched_film_id"] is None
        assert row["match_score"] == 0


def test_delete_film_returns_404_for_missing(client_db):
    client, _ = client_db
    r = client.delete("/api/films/does-not-exist")
    assert r.status_code == 404


def test_delete_film_survives_vector_store_outage(client_db):
    """Qdrant unreachable should NOT block the SQL delete (DB is source of truth)."""
    client, db_path = client_db

    with patch("backend.routers.films.get_qdrant_client", side_effect=RuntimeError("qdrant down")):
        r = client.delete("/api/films/f-1")

    assert r.status_code == 200, r.text
    assert r.json()["vector_deleted"] is False
    with get_db(db_path) as conn:
        assert get_film(conn, "f-1") is None
