"""Unit tests for the neutral seed loader (scripts/seed_from_file.py).

parse_film is pure (no IO) so we test it directly. A sync-gate test keeps
data/films.seed.json and the MOCK_FILMS fixture from drifting — they are the
same knowledge (the mock dataset), two representations.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.award_view import list_nominees_with_films
from backend.tests.fixtures.mock_films import MOCK_FILMS, seed_mock_db
from scripts.seed_from_file import parse_film, primary_title, seed_awards

_VALID = {"comedy", "drama", "sci-fi", "romance"}


def test_primary_title_locale_order():
    assert primary_title({"zh": "中", "en": "EN"}) == "中"  # zh wins
    assert primary_title({"en": "EN", "ja": "JA"}) == "EN"  # en next
    assert primary_title({"ja": "JA"}) == "JA"  # fallback first


def test_parse_film_string_tags_default_confidence():
    out = parse_film({"id": "f1", "titles": {"zh": "片"}, "tags": ["comedy"]}, _VALID)
    assert out["tags"] == [("comedy", 1.0)]
    assert out["title_zh"] == "片" and out["title_en"] is None


def test_parse_film_object_tags_confidence():
    out = parse_film(
        {"id": "f1", "titles": {"zh": "片"}, "tags": [{"tag_id": "drama", "confidence": 0.8}]},
        _VALID,
    )
    assert out["tags"] == [("drama", 0.8)]


def test_parse_film_drops_unknown_tags():
    out = parse_film({"id": "f1", "titles": {"zh": "片"}, "tags": ["comedy", "not-a-tag"]}, _VALID)
    assert out["tags"] == [("comedy", 1.0)]
    assert out["dropped_tags"] == ["not-a-tag"]


def test_parse_film_no_tags():
    out = parse_film({"id": "f1", "titles": {"en": "X"}}, _VALID)
    assert out["tags"] == [] and out["dropped_tags"] == []
    assert out["title_zh"] == "X"  # falls back to en when no zh


def test_parse_film_optional_fields_mapped():
    out = parse_film(
        {
            "id": "f1",
            "titles": {"zh": "片", "en": "Film"},
            "description": "desc",
            "poster": "data:image/svg+xml,x",
            "url": "https://example.com/f1",
            "year": 2024,
            "country": ["US", "KR"],
            "director": "D",
            "cast": "C",
            "tags": [],
        },
        _VALID,
    )
    assert out["release_year"] == 2024
    assert out["country_codes"] == "US,KR"  # ISO list -> CSV
    assert out["tmdb_director"] == "D" and out["tmdb_cast"] == "C"
    assert out["catchplay_url"] == "https://example.com/f1"
    assert out["poster_url"] == "data:image/svg+xml,x"


def test_seed_file_in_sync_with_fixture():
    """data/films.seed.json must stay aligned with MOCK_FILMS (same mock knowledge)."""
    doc = json.loads(Path("data/films.seed.json").read_text(encoding="utf-8"))
    assert doc["version"] == 1
    by_id = {f["id"]: f for f in doc["films"]}
    assert set(by_id) == {f["film_id"] for f in MOCK_FILMS}
    for mf in MOCK_FILMS:
        sf = by_id[mf["film_id"]]
        assert sf["titles"]["zh"] == mf["title_zh"]
        assert sf["description"] == mf["description"]
        assert sf["tags"] == mf["tags"]


def _write_awards(tmp_path: Path, nominees: list[dict], org_id: str = "oscars") -> Path:
    p = tmp_path / "awards.seed.json"
    p.write_text(
        json.dumps(
            {"version": 1, "ceremonies": [{"org_id": org_id, "year": 2025, "nominees": nominees}]}
        ),
        encoding="utf-8",
    )
    return p


def test_seed_awards_matches_films_by_title(test_conn, tmp_path):
    """Nominees whose title matches a seeded film get an in-library match."""
    seed_mock_db(test_conn)
    path = _write_awards(
        tmp_path,
        [
            {"category": "Best Picture", "film_title_primary": "機械叛變", "result": "won"},
            {"category": "Best Actor", "film_title_primary": "午夜來電", "result": "nominated"},
        ],
    )
    assert seed_awards(test_conn, path) == 2
    by_title = {
        n.film_title_primary: n for n in list_nominees_with_films(test_conn, org_id="oscars")
    }
    assert by_title["機械叛變"].matched_film_id == "mock-010"
    assert by_title["機械叛變"].result == "won"
    assert by_title["午夜來電"].matched_film_id == "mock-002"


def test_seed_awards_missing_file_is_noop(test_conn, tmp_path):
    assert seed_awards(test_conn, tmp_path / "absent.json") == 0


def test_seed_awards_idempotent_on_reseed(test_conn, tmp_path):
    """Re-running the seed (make seed twice) must not duplicate nominees — incl.
    person-less ones (Best Picture), whose NULL person used to dodge the upsert's
    ON CONFLICT and inflate /awards counts on every reseed."""
    seed_mock_db(test_conn)
    path = _write_awards(
        tmp_path,
        [
            {"category": "Best Picture", "film_title_primary": "機械叛變", "result": "won"},
            {
                "category": "Best Actor",
                "film_title_primary": "午夜來電",
                "person": "Mock Actor",
                "result": "nominated",
            },
        ],
    )
    seed_awards(test_conn, path)
    first = test_conn.execute("SELECT count(*) FROM award_nominees").fetchone()[0]
    seed_awards(test_conn, path)  # reseed
    second = test_conn.execute("SELECT count(*) FROM award_nominees").fetchone()[0]
    assert first == second == 2


def test_seed_awards_repairs_preexisting_null_person_rows(test_conn, tmp_path):
    """A DB seeded before the person-normalize fix holds NULL-person rows; an
    in-place reseed must clear them (DELETE-per-ceremony), not leave dups beside
    the new empty-string rows."""
    seed_mock_db(test_conn)
    path = _write_awards(
        tmp_path,
        [{"category": "Best Picture", "film_title_primary": "機械叛變", "result": "won"}],
    )
    seed_awards(test_conn, path)
    r = test_conn.execute(
        "SELECT org_id, tag_id, year, category, film_title_primary FROM award_nominees"
    ).fetchone()
    # Simulate a stale pre-patch duplicate: same ceremony, person IS NULL (which
    # the upsert's conflict key treats as distinct from the new "" row).
    test_conn.execute(
        "INSERT INTO award_nominees (org_id, tag_id, year, category, film_title_primary, person, result)"
        " VALUES (?,?,?,?,?,NULL,'won')",
        (r["org_id"], r["tag_id"], r["year"], r["category"], r["film_title_primary"]),
    )
    test_conn.commit()
    assert test_conn.execute("SELECT count(*) FROM award_nominees").fetchone()[0] == 2
    seed_awards(test_conn, path)  # reseed replaces the ceremony
    assert test_conn.execute("SELECT count(*) FROM award_nominees").fetchone()[0] == 1


def test_seed_awards_unknown_org_skipped(test_conn, tmp_path):
    """An unknown org is skipped (warned), never fatal."""
    seed_mock_db(test_conn)
    path = _write_awards(
        tmp_path,
        [{"category": "X", "film_title_primary": "機械叛變", "result": "won"}],
        org_id="not-a-real-org",
    )
    assert seed_awards(test_conn, path) == 0


def test_awards_seed_titles_in_sync_with_films():
    """Every data/awards.seed.json nominee title must name a seeded film, so the
    in-library match fires — guards against the awards/films seeds drifting."""
    awards = json.loads(Path("data/awards.seed.json").read_text(encoding="utf-8"))
    assert awards["version"] == 1
    film_titles = {f["title_zh"] for f in MOCK_FILMS}
    for cer in awards["ceremonies"]:
        for nom in cer["nominees"]:
            assert nom["film_title_primary"] in film_titles, nom["film_title_primary"]
