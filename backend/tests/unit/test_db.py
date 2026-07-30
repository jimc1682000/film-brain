"""Unit tests for backend/db.py CRUD helpers."""

import sqlite3

import pytest

from backend.db import (
    delete_film_tag,
    delete_film_tags_by_source,
    get_all_films,
    get_db,
    get_dimension_stats,
    get_film,
    get_film_tags,
    get_films_by_tag,
    get_reviews_for_film,
    get_tag_reject_stats,
    get_tags_by_dimension,
    insert_film,
    insert_film_tag,
    insert_tag,
    insert_tag_review,
    update_film_tag_source,
)

# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------


def test_init_db_creates_tables(test_db):
    """init_db must create all four expected tables."""
    conn = sqlite3.connect(str(test_db))
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    table_names = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert "tags" in table_names
    assert "films" in table_names
    assert "film_tags" in table_names
    assert "tag_reviews" in table_names


def test_init_db_creates_indexes(test_db):
    """init_db must create the three performance indexes."""
    conn = sqlite3.connect(str(test_db))
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index' ORDER BY name")
    index_names = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert "idx_film_tags_film" in index_names
    assert "idx_film_tags_tag" in index_names
    assert "idx_tags_dimension" in index_names


# ---------------------------------------------------------------------------
# user_version migrations
# ---------------------------------------------------------------------------


def _user_version(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_init_db_fresh_db_sets_user_version(test_db):
    """A brand-new DB ends up at user_version == len(MIGRATIONS)."""
    import backend.db as db_mod

    assert _user_version(test_db) == len(db_mod.MIGRATIONS)


def test_init_db_legacy_db_adopts_versioning(tmp_path):
    """A pre-scaffold DB (tables exist, user_version=0) is adopted in place:
    the idempotent base re-runs harmlessly, data survives, version is bumped."""
    import backend.db as db_mod
    from backend.db import init_db

    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    conn.executescript(db_mod.SCHEMA)  # hand-made old DB: schema, no version
    conn.execute(
        "INSERT INTO films (film_id, title_zh) VALUES (?, ?)",
        ("legacy-001", "老片"),
    )
    conn.commit()
    conn.close()
    assert _user_version(legacy) == 0

    init_db(legacy)

    assert _user_version(legacy) == len(db_mod.MIGRATIONS)
    conn = sqlite3.connect(str(legacy))
    row = conn.execute("SELECT title_zh FROM films WHERE film_id = 'legacy-001'").fetchone()
    conn.close()
    assert row[0] == "老片"  # existing data untouched


def test_init_db_repeated_runs_idempotent(test_db):
    """Re-running init_db on an up-to-date DB is a no-op (no error, same version)."""
    import backend.db as db_mod
    from backend.db import init_db

    init_db(test_db)
    init_db(test_db)
    assert _user_version(test_db) == len(db_mod.MIGRATIONS)


def test_init_db_applies_only_pending_migrations(test_db, monkeypatch):
    """An already-initialized DB applies just the appended MIGRATIONS tail."""
    import backend.db as db_mod
    from backend.db import init_db

    base_count = len(db_mod.MIGRATIONS)
    extra = "CREATE TABLE IF NOT EXISTS migration_probe (id INTEGER PRIMARY KEY);"
    monkeypatch.setattr(db_mod, "MIGRATIONS", [*db_mod.MIGRATIONS, extra])
    init_db(test_db)

    conn = sqlite3.connect(str(test_db))
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    assert "migration_probe" in tables
    assert _user_version(test_db) == base_count + 1


# ---------------------------------------------------------------------------
# Tag CRUD
# ---------------------------------------------------------------------------


def test_insert_and_get_tag(test_conn, sample_tag):
    """insert_tag then get_tags_by_dimension should return the inserted row."""
    insert_tag(
        test_conn,
        tag_id=sample_tag["tag_id"],
        dimension=sample_tag["dimension"],
        label_en=sample_tag["label_en"],
        label_zh_tw=sample_tag["label_zh_tw"],
        source=sample_tag["source"],
        status=sample_tag["status"],
    )
    test_conn.commit()

    tags = get_tags_by_dimension(test_conn, sample_tag["dimension"])
    assert len(tags) == 1
    tag = tags[0]
    assert tag["tag_id"] == sample_tag["tag_id"]
    assert tag["dimension"] == sample_tag["dimension"]
    assert tag["label_en"] == sample_tag["label_en"]
    assert tag["label_zh_tw"] == sample_tag["label_zh_tw"]


def test_insert_duplicate_tag_ignored(test_conn, sample_tag):
    """Inserting a tag with the same tag_id twice should not raise and only keep one row."""
    for _ in range(2):
        insert_tag(
            test_conn,
            tag_id=sample_tag["tag_id"],
            dimension=sample_tag["dimension"],
            label_en=sample_tag["label_en"],
            label_zh_tw=sample_tag["label_zh_tw"],
        )
    test_conn.commit()

    tags = get_tags_by_dimension(test_conn, sample_tag["dimension"])
    assert len(tags) == 1


def test_get_tags_by_dimension_no_filter_returns_all(test_conn):
    """get_tags_by_dimension with no dimension argument returns all tags."""
    insert_tag(test_conn, "comedy", "genre", "Comedy", "喜劇")
    insert_tag(test_conn, "tearjerker", "emotion", "Tearjerker", "催淚")
    test_conn.commit()

    all_tags = get_tags_by_dimension(test_conn)
    tag_ids = {t["tag_id"] for t in all_tags}
    assert "comedy" in tag_ids
    assert "tearjerker" in tag_ids


def test_get_tags_by_dimension(test_conn):
    """get_tags_by_dimension with a dimension filter returns only matching tags."""
    insert_tag(test_conn, "comedy", "genre", "Comedy", "喜劇")
    insert_tag(test_conn, "drama", "genre", "Drama", "劇情")
    insert_tag(test_conn, "tearjerker", "emotion", "Tearjerker", "催淚")
    test_conn.commit()

    genre_tags = get_tags_by_dimension(test_conn, "genre")
    assert len(genre_tags) == 2
    dimensions_returned = {t["dimension"] for t in genre_tags}
    assert dimensions_returned == {"genre"}


def test_get_dimension_stats(test_conn):
    """get_dimension_stats returns per-dimension active tag counts."""
    insert_tag(test_conn, "comedy", "genre", "Comedy", "喜劇", status="active")
    insert_tag(test_conn, "drama", "genre", "Drama", "劇情", status="active")
    insert_tag(test_conn, "tearjerker", "emotion", "Tearjerker", "催淚", status="active")
    insert_tag(test_conn, "deprecated-tag", "genre", "Deprecated", "棄用", status="deprecated")
    test_conn.commit()

    stats = get_dimension_stats(test_conn)
    stats_by_dim = {s["dimension"]: s["tag_count"] for s in stats}

    # deprecated tags must not be counted
    assert stats_by_dim["genre"] == 2
    assert stats_by_dim["emotion"] == 1


# ---------------------------------------------------------------------------
# Film CRUD
# ---------------------------------------------------------------------------


def test_insert_and_get_film(test_conn, sample_film):
    """insert_film then get_film should return the correct film dict."""
    insert_film(test_conn, **sample_film)
    test_conn.commit()

    film = get_film(test_conn, sample_film["film_id"])
    assert film is not None
    assert film["film_id"] == sample_film["film_id"]
    assert film["title_zh"] == sample_film["title_zh"]
    assert film["title_en"] == sample_film["title_en"]


def test_get_film_returns_none_for_missing_id(test_conn):
    """get_film must return None when the film_id does not exist."""
    result = get_film(test_conn, "nonexistent-film-id")
    assert result is None


def test_insert_duplicate_film_ignored(test_conn, sample_film):
    """Inserting the same film_id twice should leave only one row."""
    insert_film(test_conn, **sample_film)
    modified = {**sample_film, "title_zh": "不同標題"}
    insert_film(test_conn, **modified)
    test_conn.commit()

    all_films = get_all_films(test_conn)
    assert len(all_films) == 1
    # First insert wins (INSERT OR IGNORE)
    assert all_films[0]["title_zh"] == sample_film["title_zh"]


def test_get_all_films_returns_ordered_by_title(test_conn):
    """get_all_films must return rows ordered by title_zh."""
    insert_film(test_conn, film_id="film-z", title_zh="Z影片")
    insert_film(test_conn, film_id="film-a", title_zh="A影片")
    test_conn.commit()

    films = get_all_films(test_conn)
    assert len(films) == 2
    assert films[0]["title_zh"] == "A影片"
    assert films[1]["title_zh"] == "Z影片"


# ---------------------------------------------------------------------------
# Film-tag relationship CRUD
# ---------------------------------------------------------------------------


def test_insert_film_tag_and_get_film_tags(test_conn, sample_film, sample_tag):
    """insert_film_tag links a film and tag; get_film_tags returns the association with tag details."""
    insert_tag(
        test_conn,
        tag_id=sample_tag["tag_id"],
        dimension=sample_tag["dimension"],
        label_en=sample_tag["label_en"],
        label_zh_tw=sample_tag["label_zh_tw"],
    )
    insert_film(test_conn, **sample_film)
    insert_film_tag(
        test_conn,
        film_id=sample_film["film_id"],
        tag_id=sample_tag["tag_id"],
        confidence=0.9,
        source="auto",
    )
    test_conn.commit()

    film_tags = get_film_tags(test_conn, sample_film["film_id"])
    assert len(film_tags) == 1
    ft = film_tags[0]
    assert ft["tag_id"] == sample_tag["tag_id"]
    assert ft["film_id"] == sample_film["film_id"]
    assert ft["confidence"] == pytest.approx(0.9)
    assert ft["dimension"] == sample_tag["dimension"]
    assert ft["label_en"] == sample_tag["label_en"]
    assert ft["label_zh_tw"] == sample_tag["label_zh_tw"]


def test_get_film_tags_orders_by_confidence_desc(test_conn, sample_film):
    """List-view tag slices (tags[:5] in film_card) must surface the most
    confident signals first, regardless of dimension alphabet order. Fixes
    an editor's "為什麼是這 5 個" question — previously dim ASC put audience
    before emotion/theme even when their confidence was much lower.
    """
    insert_tag(test_conn, "audience-kid", "audience", "Kid", "兒童")
    insert_tag(test_conn, "emotion-tear", "emotion", "Tearjerker", "催淚")
    insert_tag(test_conn, "theme-family", "theme", "Family", "家庭")
    insert_film(test_conn, **sample_film)
    fid = sample_film["film_id"]
    insert_film_tag(test_conn, fid, "audience-kid", confidence=0.3, source="ai")
    insert_film_tag(test_conn, fid, "emotion-tear", confidence=0.95, source="ai")
    insert_film_tag(test_conn, fid, "theme-family", confidence=0.8, source="ai")
    test_conn.commit()

    rows = get_film_tags(test_conn, fid)
    assert [r["tag_id"] for r in rows] == [
        "emotion-tear",
        "theme-family",
        "audience-kid",
    ]


def test_get_film_tags_empty_for_untagged_film(test_conn, sample_film):
    """get_film_tags must return an empty list when a film has no tags."""
    insert_film(test_conn, **sample_film)
    test_conn.commit()

    film_tags = get_film_tags(test_conn, sample_film["film_id"])
    assert film_tags == []


def test_get_films_by_tag(test_conn, sample_film, sample_tag):
    """get_films_by_tag returns all films associated with a given tag."""
    insert_tag(
        test_conn,
        tag_id=sample_tag["tag_id"],
        dimension=sample_tag["dimension"],
        label_en=sample_tag["label_en"],
        label_zh_tw=sample_tag["label_zh_tw"],
    )
    insert_film(test_conn, **sample_film)
    insert_film_tag(
        test_conn,
        film_id=sample_film["film_id"],
        tag_id=sample_tag["tag_id"],
        confidence=0.85,
        source="migrated",
    )
    test_conn.commit()

    films = get_films_by_tag(test_conn, sample_tag["tag_id"])
    assert len(films) == 1
    assert films[0]["film_id"] == sample_film["film_id"]
    assert films[0]["confidence"] == pytest.approx(0.85)
    assert films[0]["tag_source"] == "migrated"


def test_get_films_by_tag_ordered_by_confidence_desc(test_conn, sample_tag):
    """get_films_by_tag must return films ordered by confidence descending."""
    insert_tag(
        test_conn,
        tag_id=sample_tag["tag_id"],
        dimension=sample_tag["dimension"],
        label_en=sample_tag["label_en"],
        label_zh_tw=sample_tag["label_zh_tw"],
    )
    insert_film(test_conn, film_id="film-low", title_zh="低信心影片")
    insert_film(test_conn, film_id="film-high", title_zh="高信心影片")
    insert_film_tag(test_conn, film_id="film-low", tag_id=sample_tag["tag_id"], confidence=0.3)
    insert_film_tag(test_conn, film_id="film-high", tag_id=sample_tag["tag_id"], confidence=0.95)
    test_conn.commit()

    films = get_films_by_tag(test_conn, sample_tag["tag_id"])
    assert films[0]["film_id"] == "film-high"
    assert films[1]["film_id"] == "film-low"


def test_get_films_by_tag_returns_empty_list_for_unused_tag(test_conn, sample_tag):
    """get_films_by_tag returns an empty list when no film carries that tag."""
    insert_tag(
        test_conn,
        tag_id=sample_tag["tag_id"],
        dimension=sample_tag["dimension"],
        label_en=sample_tag["label_en"],
        label_zh_tw=sample_tag["label_zh_tw"],
    )
    test_conn.commit()

    films = get_films_by_tag(test_conn, sample_tag["tag_id"])
    assert films == []


# ---------------------------------------------------------------------------
# get_db context manager
# ---------------------------------------------------------------------------


def test_get_db_yields_row_factory_connection(test_db):
    """get_db context manager must yield a connection with Row factory set."""
    with get_db(test_db) as conn:
        conn.execute(
            "INSERT INTO tags (tag_id, dimension, label_en, label_zh_tw) "
            "VALUES ('test-tag', 'genre', 'Test', '測試')"
        )
        row = conn.execute("SELECT * FROM tags WHERE tag_id = 'test-tag'").fetchone()

    # sqlite3.Row supports key-based access
    assert row["tag_id"] == "test-tag"
    assert row["dimension"] == "genre"


def test_get_db_rollbacks_on_exception(test_db):
    """get_db must roll back the transaction when an exception is raised inside the block."""
    try:
        with get_db(test_db) as conn:
            conn.execute(
                "INSERT INTO tags (tag_id, dimension, label_en, label_zh_tw) "
                "VALUES ('rollback-tag', 'genre', 'Rollback', '回滾')"
            )
            raise RuntimeError("simulated failure")
    except RuntimeError:
        pass

    with get_db(test_db) as conn:
        row = conn.execute("SELECT * FROM tags WHERE tag_id = 'rollback-tag'").fetchone()
    assert row is None


# ---------------------------------------------------------------------------
# Review helpers
# ---------------------------------------------------------------------------


def _seed_film_and_tag(conn, sample_film, sample_tag):
    insert_tag(
        conn,
        tag_id=sample_tag["tag_id"],
        dimension=sample_tag["dimension"],
        label_en=sample_tag["label_en"],
        label_zh_tw=sample_tag["label_zh_tw"],
    )
    insert_film(conn, **sample_film)
    insert_film_tag(
        conn,
        film_id=sample_film["film_id"],
        tag_id=sample_tag["tag_id"],
        confidence=0.9,
        source="ai",
    )
    conn.commit()


def test_insert_tag_review_creates_row(test_conn, sample_film, sample_tag):
    _seed_film_and_tag(test_conn, sample_film, sample_tag)

    insert_tag_review(
        test_conn,
        film_id=sample_film["film_id"],
        tag_id=sample_tag["tag_id"],
        action="approved",
        reviewer="editor",
    )
    test_conn.commit()

    rows = get_reviews_for_film(test_conn, sample_film["film_id"])
    assert len(rows) == 1
    assert rows[0]["action"] == "approved"
    assert rows[0]["reviewer"] == "editor"


def test_delete_film_tag_removes_row(test_conn, sample_film, sample_tag):
    _seed_film_and_tag(test_conn, sample_film, sample_tag)

    deleted = delete_film_tag(test_conn, sample_film["film_id"], sample_tag["tag_id"])
    test_conn.commit()

    assert deleted == 1
    assert get_film_tags(test_conn, sample_film["film_id"]) == []


def test_delete_film_tags_by_source_only_targets_matching_source(
    test_conn, sample_film, sample_tag
):
    _seed_film_and_tag(test_conn, sample_film, sample_tag)  # source=ai
    insert_tag(
        test_conn,
        tag_id="curation-hkfa-2099-nominee",
        dimension="curation-award",
        label_en="HKFA 2099 nominee",
        label_zh_tw="香港金像 2099 入圍",
    )
    insert_film_tag(
        test_conn,
        film_id=sample_film["film_id"],
        tag_id="curation-hkfa-2099-nominee",
        confidence=1.0,
        source="award-curation",
    )
    test_conn.commit()

    deleted = delete_film_tags_by_source(test_conn, sample_film["film_id"], "ai")
    test_conn.commit()

    assert deleted == 1
    remaining = get_film_tags(test_conn, sample_film["film_id"])
    assert len(remaining) == 1
    assert remaining[0]["source"] == "award-curation"


def test_update_film_tag_source(test_conn, sample_film, sample_tag):
    _seed_film_and_tag(test_conn, sample_film, sample_tag)

    updated = update_film_tag_source(
        test_conn, sample_film["film_id"], sample_tag["tag_id"], "human-approved"
    )
    test_conn.commit()

    assert updated == 1
    rows = get_film_tags(test_conn, sample_film["film_id"])
    assert rows[0]["source"] == "human-approved"


def test_reject_stats_min_reviews_filter(test_conn, sample_film, sample_tag):
    _seed_film_and_tag(test_conn, sample_film, sample_tag)

    # 2 rejections, 1 approval — 3 total, 67% reject rate
    for action in ("rejected", "rejected", "approved"):
        insert_tag_review(test_conn, sample_film["film_id"], sample_tag["tag_id"], action=action)
    test_conn.commit()

    stats = get_tag_reject_stats(test_conn, min_reviews=3)
    assert len(stats) == 1
    assert stats[0]["tag_id"] == sample_tag["tag_id"]
    assert stats[0]["total_reviews"] == 3
    assert stats[0]["rejected"] == 2
    assert stats[0]["reject_rate"] == pytest.approx(2 / 3)

    # Raising threshold excludes the tag
    assert get_tag_reject_stats(test_conn, min_reviews=10) == []
