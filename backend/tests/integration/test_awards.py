"""Integration tests for /api/awards endpoints.

The awards surface had no test coverage before this file and is the
hottest demo path — distinct-film count semantics, fuzzy-match matched_film_id
linking, curation-tag side effects, and the poster fallback all need to
keep working through future refactors.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.db import get_db, init_db, insert_film, insert_tag
from backend.main import app


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    monkeypatch.setattr("backend.config.settings.db_path", db_path)
    monkeypatch.setattr("backend.db.settings.db_path", db_path)

    # Two films, two nominees from a ceremony where one film sweeps two
    # categories and the other only earned a nomination in one category.
    with get_db(db_path) as conn:
        insert_tag(conn, "golden-horse-best-picture", "award", "Best Picture", "最佳劇情片")
        insert_tag(conn, "golden-horse-best-director", "award", "Best Director", "最佳導演")
        insert_film(
            conn,
            film_id="f-sweep",
            title_zh="我家的事",
            title_en="My Family",
            description="…",
            tmdb_id=1,
        )
        insert_film(
            conn,
            film_id="f-other",
            title_zh="進行曲",
            title_en="Marching Boys",
            description="…",
            tmdb_id=2,
        )
        # Nominee rows hand-crafted (we are not exercising record_nomination here;
        # that requires the TMDB HTTP path).
        conn.executemany(
            "INSERT INTO award_nominees ("
            " org_id, tag_id, year, category, film_title_primary, result, "
            " matched_film_id, match_score, tmdb_id, tmdb_title, tmdb_poster_url"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "golden-horse",
                    "golden-horse-best-picture",
                    2025,
                    "Best Picture",
                    "我家的事",
                    "won",
                    "f-sweep",
                    1.0,
                    1,
                    "我家的事",
                    "https://image.tmdb.org/family.jpg",
                ),
                (
                    "golden-horse",
                    "golden-horse-best-director",
                    2025,
                    "Best Director",
                    "我家的事",
                    "nominated",
                    "f-sweep",
                    1.0,
                    1,
                    "我家的事",
                    "https://image.tmdb.org/family.jpg",
                ),
                (
                    "golden-horse",
                    "golden-horse-best-director",
                    2025,
                    "Best Director",
                    "進行曲",
                    "nominated",
                    "f-other",
                    1.0,
                    2,
                    "進行曲",
                    "https://image.tmdb.org/marching.jpg",
                ),
                (
                    "golden-horse",
                    "golden-horse-best-director",
                    2025,
                    "Best Director",
                    "未列入片庫的片",
                    "nominated",
                    None,
                    0.0,
                    99,
                    "Unlisted",
                    "https://image.tmdb.org/unlisted.jpg",
                ),
            ],
        )

    return TestClient(app), db_path


def test_orgs_endpoint_returns_registry(client_db):
    client, _ = client_db
    r = client.get("/api/awards/orgs")
    assert r.status_code == 200
    org_ids = {o["org_id"] for o in r.json()}
    # awards-registry.json carries Oscar / Golden Horse / Cannes etc.
    assert "oscars" in org_ids
    assert "golden-horse" in org_ids


def test_recent_batches_distinct_film_counts(client_db):
    """The ceremony header counts must be distinct films, not nomination rows.

    Two rows of 我家的事 + one of 進行曲 + one unmatched = 3 distinct
    nominated films (one unmatched, two in library). Won films distinct = 1.
    """
    client, _ = client_db
    r = client.get("/api/awards/recent-batches?limit=10")
    assert r.status_code == 200
    rows = r.json()
    by_tag = {(b["tag_id"], b["year"]): b for b in rows}

    director = by_tag[("golden-horse-best-director", 2025)]
    assert director["org_id"] == "golden-horse"
    # 我家的事 + 進行曲 + 未列入片庫的片 = 3 distinct nominated film titles
    assert director["ceremony_nominated_films_total"] == 3
    # 我家的事 + 進行曲 = 2 distinct films in library
    assert director["ceremony_nominated_films_matched"] == 2
    # only Best Picture has a won row, and only one film — 我家的事
    assert director["ceremony_won_films_total"] == 1
    assert director["ceremony_won_films_matched"] == 1


def test_nominees_stitches_library_poster(client_db):
    """A matched nominee should carry the library's title + poster, so the
    awards page renders the same artwork as the film detail page."""
    client, db_path = client_db

    # Give f-sweep a CATCHPLAY+ poster distinct from the TMDB one above.
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE films SET poster_url = ? WHERE film_id = ?",
            ("https://catchplay.com/family.jpg", "f-sweep"),
        )

    r = client.get("/api/awards/nominees?org_id=golden-horse&year=2025")
    assert r.status_code == 200
    by_id = {n["film_title_primary"]: n for n in r.json() if n["category"] == "Best Picture"}
    sweep = by_id["我家的事"]
    assert sweep["matched_film_id"] == "f-sweep"
    assert sweep["matched_title_zh"] == "我家的事"
    assert sweep["matched_poster_url"] == "https://catchplay.com/family.jpg"
    # TMDB poster still exposed as fallback for the frontend chain.
    assert sweep["tmdb_poster_url"] == "https://image.tmdb.org/family.jpg"


def test_nominees_unmatched_passes_through(client_db):
    """Unmatched nominees should still surface — they are the 'not in library
    yet' bucket the awards card shows with 片庫無 badge."""
    client, _ = client_db
    r = client.get("/api/awards/nominees?org_id=golden-horse&year=2025")
    rows = r.json()
    unlisted = next(n for n in rows if n["film_title_primary"] == "未列入片庫的片")
    assert unlisted["matched_film_id"] is None
    assert unlisted["matched_poster_url"] is None


def test_orgs_endpoint_rejects_unknown_org_on_nominees(client_db):
    client, _ = client_db
    r = client.get("/api/awards/nominees?org_id=fake-award")
    assert r.status_code == 404


def test_ingest_unknown_org_returns_404(client_db):
    client, _ = client_db
    r = client.post(
        "/api/awards/ingest",
        json={
            "org_id": "no-such",
            "year": 2025,
            "source_url": "https://example.com",
            "nominees": [],
        },
    )
    assert r.status_code == 404


def test_ingest_matched_and_unmatched_split(client_db, tmp_path, monkeypatch):
    """ingest splits results into matched / unmatched and emits a curation tag
    on the matched film. record_nomination's TMDB lookup is patched so the
    test does not hit the network."""
    client, db_path = client_db

    with patch("backend.award_manager.fetch_tmdb_by_id", return_value=None):
        with patch("backend.award_manager.search_tmdb", return_value=None):
            r = client.post(
                "/api/awards/ingest",
                json={
                    "org_id": "golden-horse",
                    "year": 2026,
                    "source_url": "https://example.com/golden-horse-2026",
                    "nominees": [
                        {
                            "film_title_primary": "我家的事",
                            "category": "Best Picture",
                            "result": "won",
                        },
                        {
                            "film_title_primary": "片庫沒有的片",
                            "category": "Best Picture",
                            "result": "nominated",
                        },
                    ],
                },
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_nominees"] == 2
    assert len(body["matched"]) == 1
    assert body["matched"][0]["matched_film_id"] == "f-sweep"
    assert len(body["unmatched"]) == 1

    # The matched film should now carry the curation-award tag.
    with get_db(db_path) as conn:
        tags = [
            r[0]
            for r in conn.execute(
                "SELECT tag_id FROM film_tags WHERE film_id = 'f-sweep' AND source = 'award-curation'"
            ).fetchall()
        ]
    assert any(t == "curation-golden-horse-2026-winner" for t in tags)
