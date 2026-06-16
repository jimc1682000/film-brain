"""Integration tests for the reviews router."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.db import get_db, init_db, insert_film, insert_film_tag, insert_tag
from backend.main import app
from backend.routers import reviews as reviews_router


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    """Spin up TestClient backed by an isolated SQLite file.

    Patches backend.config.settings.db_path and backend.db.settings.db_path
    so every get_db() call in the router hits the tmp DB. Also stubs
    TagRegistry.all_tag_ids since the router validates tag_ids against it.
    """
    db_path = tmp_path / "test.db"
    init_db(db_path)

    monkeypatch.setattr("backend.config.settings.db_path", db_path)
    monkeypatch.setattr("backend.db.settings.db_path", db_path)

    with get_db(db_path) as conn:
        insert_tag(conn, "thriller", "genre", "Thriller", "驚悚")
        insert_tag(conn, "mystery", "genre", "Mystery", "懸疑")
        insert_film(
            conn,
            film_id="f-1",
            title_zh="測試片",
            title_en="Test",
            description="d",
        )
        insert_film_tag(conn, "f-1", "thriller", confidence=0.9, source="ai")

    fake_registry = type("FakeRegistry", (), {"all_tag_ids": {"thriller", "mystery"}})()
    with patch.object(reviews_router, "_registry", fake_registry):
        yield TestClient(app), db_path


def test_approve_flips_source_and_records_review(client_db):
    client, db_path = client_db
    resp = client.post(
        "/api/films/f-1/reviews",
        json={"tag_id": "thriller", "action": "approved"},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "approved"

    with get_db(db_path) as conn:
        tag_row = conn.execute(
            "SELECT source FROM film_tags WHERE film_id='f-1' AND tag_id='thriller'"
        ).fetchone()
        assert tag_row["source"] == "human-approved"

    reviews = client.get("/api/films/f-1/reviews").json()
    assert len(reviews) == 1
    assert reviews[0]["action"] == "approved"


def test_reject_deletes_tag_but_keeps_review(client_db):
    client, db_path = client_db
    resp = client.post(
        "/api/films/f-1/reviews",
        json={"tag_id": "thriller", "action": "rejected"},
    )
    assert resp.status_code == 200

    with get_db(db_path) as conn:
        tag_row = conn.execute(
            "SELECT 1 FROM film_tags WHERE film_id='f-1' AND tag_id='thriller'"
        ).fetchone()
        assert tag_row is None

    reviews = client.get("/api/films/f-1/reviews").json()
    assert reviews[0]["action"] == "rejected"


def test_modify_swaps_tag_and_records_two_reviews(client_db):
    client, db_path = client_db
    resp = client.post(
        "/api/films/f-1/reviews",
        json={
            "tag_id": "thriller",
            "action": "modified",
            "replacement_tag_id": "mystery",
            "replacement_confidence": 0.85,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["replacement_tag_id"] == "mystery"

    with get_db(db_path) as conn:
        rows = conn.execute("SELECT tag_id, source FROM film_tags WHERE film_id='f-1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["tag_id"] == "mystery"
    assert rows[0]["source"] == "manual"

    actions = [r["action"] for r in client.get("/api/films/f-1/reviews").json()]
    assert "modified" in actions
    assert "approved" in actions


def test_modify_requires_replacement_tag_id(client_db):
    client, _ = client_db
    resp = client.post(
        "/api/films/f-1/reviews",
        json={"tag_id": "thriller", "action": "modified"},
    )
    assert resp.status_code == 400


def test_unknown_tag_id_rejected(client_db):
    client, _ = client_db
    resp = client.post(
        "/api/films/f-1/reviews",
        json={"tag_id": "does-not-exist", "action": "approved"},
    )
    assert resp.status_code == 400


def test_film_not_found(client_db):
    client, _ = client_db
    resp = client.post(
        "/api/films/missing/reviews",
        json={"tag_id": "thriller", "action": "approved"},
    )
    assert resp.status_code == 404


def test_review_stats(client_db):
    client, db_path = client_db
    # Need ≥3 reviews on same tag to appear
    with get_db(db_path) as conn:
        for action in ("rejected", "rejected", "approved"):
            conn.execute(
                "INSERT INTO tag_reviews (film_id, tag_id, action, reviewer) "
                "VALUES ('f-1', 'thriller', ?, 'editor')",
                (action,),
            )

    stats = client.get("/api/reviews/stats?min_reviews=3").json()
    assert len(stats) == 1
    assert stats[0]["tag_id"] == "thriller"
    assert stats[0]["rejected"] == 2
    assert stats[0]["total_reviews"] == 3
