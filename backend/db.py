import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from backend.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS tags (
    tag_id       TEXT PRIMARY KEY,
    dimension    TEXT NOT NULL,
    label_en     TEXT NOT NULL,
    label_zh_tw  TEXT NOT NULL,
    label_in_id  TEXT,
    source       TEXT DEFAULT 'migrated',
    status       TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS films (
    film_id          TEXT PRIMARY KEY,
    title_zh         TEXT NOT NULL,
    title_en         TEXT,
    description      TEXT,
    description_raw  TEXT,
    catchplay_url    TEXT,
    poster_url       TEXT,
    original_genre   TEXT,
    release_year     INTEGER,
    country_codes    TEXT,
    tmdb_id          INTEGER,
    tmdb_overview    TEXT,
    tmdb_genres      TEXT,
    tmdb_keywords    TEXT,
    tmdb_vote_avg    REAL,
    tmdb_cast        TEXT,
    tmdb_director    TEXT,
    tmdb_backdrop_url TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS film_tags (
    film_id      TEXT NOT NULL REFERENCES films(film_id),
    tag_id       TEXT NOT NULL REFERENCES tags(tag_id),
    confidence   REAL NOT NULL DEFAULT 1.0,
    source       TEXT NOT NULL,
    award_year   INTEGER,
    award_result TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (film_id, tag_id)
);

CREATE TABLE IF NOT EXISTS award_nominees (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id              TEXT NOT NULL,
    tag_id              TEXT NOT NULL,
    year                INTEGER NOT NULL,
    category            TEXT NOT NULL,
    film_title_primary  TEXT NOT NULL,
    film_title_alt      TEXT,
    person              TEXT,
    result              TEXT NOT NULL,
    source_url          TEXT,
    ceremony_date       TEXT,
    tmdb_id             INTEGER,
    tmdb_media_type     TEXT,
    tmdb_title          TEXT,
    tmdb_original_title TEXT,
    tmdb_year           INTEGER,
    tmdb_poster_url     TEXT,
    tmdb_overview       TEXT,
    tmdb_vote_avg       REAL,
    matched_film_id     TEXT,
    match_score         REAL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tag_id, year, film_title_primary, person)
);

CREATE INDEX IF NOT EXISTS idx_award_nominees_tag_year ON award_nominees(tag_id, year);
CREATE INDEX IF NOT EXISTS idx_award_nominees_org_year ON award_nominees(org_id, year);
CREATE INDEX IF NOT EXISTS idx_award_nominees_matched ON award_nominees(matched_film_id);

CREATE TABLE IF NOT EXISTS tag_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    film_id     TEXT NOT NULL,
    tag_id      TEXT NOT NULL,
    action      TEXT NOT NULL,
    reviewer    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_film_tags_film ON film_tags(film_id);
CREATE INDEX IF NOT EXISTS idx_film_tags_tag ON film_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_tags_dimension ON tags(dimension);

-- Lexical (BM25) full-text index. Content is jieba-segmented (space-joined)
-- so the unicode61 tokenizer sees real CJK words; rebuilt from films by
-- backend.services.bm25_search.rebuild_fts().
CREATE VIRTUAL TABLE IF NOT EXISTS films_fts USING fts5(
    film_id UNINDEXED,
    content,
    tokenize='unicode61'
);

-- Precomputed similar films (full BM25+vector→RRF→CE pipeline run offline by
-- scripts/05_compute_similar.py). Served as a cheap lookup so the detail page
-- doesn't pay the 30-40s cross-encoder cost at request time.
CREATE TABLE IF NOT EXISTS similar_films (
    film_id         TEXT NOT NULL,
    similar_film_id TEXT NOT NULL,
    rank            INTEGER NOT NULL,
    score           REAL NOT NULL,
    PRIMARY KEY (film_id, similar_film_id)
);
CREATE INDEX IF NOT EXISTS idx_similar_films_film ON similar_films(film_id);
"""


def init_db(db_path: Path | None = None) -> None:
    """Create database and tables if they don't exist."""
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # `with sqlite3.connect(...)` commits the transaction but does NOT close the
    # connection — wrap in closing() so the handle is released (else every
    # init_db call leaks a connection: ResourceWarning under gc).
    with closing(sqlite3.connect(str(path))) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def get_db(db_path: Path | None = None):
    """Context manager for database connections with Row factory."""
    path = db_path or settings.db_path
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- CRUD helpers ---


def insert_tag(
    conn: sqlite3.Connection,
    tag_id: str,
    dimension: str,
    label_en: str,
    label_zh_tw: str,
    label_in_id: str | None = None,
    source: str = "migrated",
    status: str = "active",
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO tags (tag_id, dimension, label_en, label_zh_tw, label_in_id, source, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tag_id, dimension, label_en, label_zh_tw, label_in_id, source, status),
    )


def insert_film(conn: sqlite3.Connection, **kwargs) -> None:
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    conn.execute(
        f"INSERT OR IGNORE INTO films ({cols}) VALUES ({placeholders})",
        tuple(kwargs.values()),
    )


def insert_film_tag(
    conn: sqlite3.Connection,
    film_id: str,
    tag_id: str,
    confidence: float = 1.0,
    source: str = "migrated",
    award_year: int | None = None,
    award_result: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO film_tags (film_id, tag_id, confidence, source, award_year, award_result) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (film_id, tag_id, confidence, source, award_year, award_result),
    )


def get_film(conn: sqlite3.Connection, film_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM films WHERE film_id = ?", (film_id,)).fetchone()
    return dict(row) if row else None


def get_all_films(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM films ORDER BY title_zh").fetchall()
    return [dict(r) for r in rows]


def get_film_tags(conn: sqlite3.Connection, film_id: str) -> list[dict]:
    # confidence DESC first: list-view UIs slice the first N tags as the
    # representative summary, so the strongest signals must come first.
    # Dimension is a tiebreaker only; the detail page groups by dimension
    # itself, so global confidence ordering does not hurt readability there.
    rows = conn.execute(
        "SELECT ft.*, t.dimension, t.label_en, t.label_zh_tw "
        "FROM film_tags ft JOIN tags t ON ft.tag_id = t.tag_id "
        "WHERE ft.film_id = ? ORDER BY ft.confidence DESC, t.dimension ASC",
        (film_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_tags_by_dimension(conn: sqlite3.Connection, dimension: str | None = None) -> list[dict]:
    if dimension:
        rows = conn.execute(
            "SELECT * FROM tags WHERE dimension = ? ORDER BY tag_id", (dimension,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tags ORDER BY dimension, tag_id").fetchall()
    return [dict(r) for r in rows]


def get_dimension_stats(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT t.dimension, "
        "COUNT(DISTINCT t.tag_id) AS tag_count, "
        "COUNT(DISTINCT CASE WHEN ft.tag_id IS NOT NULL THEN t.tag_id END) AS used_tag_count "
        "FROM tags t LEFT JOIN film_tags ft ON t.tag_id = ft.tag_id "
        "WHERE t.status = 'active' "
        "GROUP BY t.dimension ORDER BY tag_count DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_films_by_tag(conn: sqlite3.Connection, tag_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT f.*, ft.confidence, ft.source as tag_source "
        "FROM films f JOIN film_tags ft ON f.film_id = ft.film_id "
        "WHERE ft.tag_id = ? ORDER BY ft.confidence DESC",
        (tag_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_tag_activity_films(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Films ordered by most recent tag insert/replace.

    Returned `tags` list is ordered by confidence DESC so the UI list-view
    slice (e.g. tags[:5]) shows the most confident signals first rather than
    arbitrary insertion-order picks — Vero asked "為什麼是這 5 個" because
    the old GROUP_CONCAT was using SQLite's undefined ordering.
    """
    rows = conn.execute(
        "WITH ordered_tags AS ( "
        "  SELECT film_id, tag_id, created_at, confidence "
        "  FROM film_tags ORDER BY confidence DESC "
        ") "
        "SELECT f.*, MAX(ot.created_at) AS last_activity, "
        "COUNT(ot.tag_id) AS tag_count, "
        "GROUP_CONCAT(ot.tag_id) AS tag_ids "
        "FROM films f JOIN ordered_tags ot ON ot.film_id = f.film_id "
        "GROUP BY f.film_id ORDER BY last_activity DESC LIMIT ?",
        (limit,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["tags"] = d.pop("tag_ids", "").split(",") if d.get("tag_ids") else []
        result.append(d)
    return result


# --- Review helpers ---


def insert_tag_review(
    conn: sqlite3.Connection, film_id: str, tag_id: str, action: str, reviewer: str = "editor"
) -> None:
    conn.execute(
        "INSERT INTO tag_reviews (film_id, tag_id, action, reviewer) VALUES (?, ?, ?, ?)",
        (film_id, tag_id, action, reviewer),
    )


def delete_film_tag(conn: sqlite3.Connection, film_id: str, tag_id: str) -> int:
    cur = conn.execute(
        "DELETE FROM film_tags WHERE film_id = ? AND tag_id = ?",
        (film_id, tag_id),
    )
    return cur.rowcount


def delete_film_tags_by_source(conn: sqlite3.Connection, film_id: str, source: str) -> int:
    """Drop all film_tags rows for a film matching an exact source value.

    Used by /save to clear stale LLM suggestions before persisting a fresh batch,
    so editors don't see ghost tags from a previous analyze pass.
    """
    cur = conn.execute(
        "DELETE FROM film_tags WHERE film_id = ? AND source = ?",
        (film_id, source),
    )
    return cur.rowcount


def delete_film(conn: sqlite3.Connection, film_id: str) -> dict:
    """Cascade-delete a film and references that would dangle without it.

    Award nominees that pointed at this film have their matched_film_id cleared
    rather than being deleted — the nominee itself (e.g. an Oscar entry) exists
    independently of whether CATCHPLAY+ carries the film.
    """
    tags_deleted = conn.execute("DELETE FROM film_tags WHERE film_id = ?", (film_id,)).rowcount
    reviews_deleted = conn.execute("DELETE FROM tag_reviews WHERE film_id = ?", (film_id,)).rowcount
    nominees_unlinked = conn.execute(
        "UPDATE award_nominees SET matched_film_id = NULL, match_score = 0 "
        "WHERE matched_film_id = ?",
        (film_id,),
    ).rowcount
    film_deleted = conn.execute("DELETE FROM films WHERE film_id = ?", (film_id,)).rowcount
    return {
        "film_deleted": film_deleted,
        "tags_deleted": tags_deleted,
        "reviews_deleted": reviews_deleted,
        "nominees_unlinked": nominees_unlinked,
    }


def update_film_tag_source(conn: sqlite3.Connection, film_id: str, tag_id: str, source: str) -> int:
    cur = conn.execute(
        "UPDATE film_tags SET source = ? WHERE film_id = ? AND tag_id = ?",
        (source, film_id, tag_id),
    )
    return cur.rowcount


def get_reviews_for_film(conn: sqlite3.Connection, film_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM tag_reviews WHERE film_id = ? ORDER BY created_at DESC",
        (film_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_award_nominee(conn: sqlite3.Connection, **kwargs) -> int:
    """Insert-or-update a nominee. Returns rowid of the affected row."""
    cols = list(kwargs.keys())
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(
        f"{c}=excluded.{c}"
        for c in cols
        if c not in ("tag_id", "year", "film_title_primary", "person")
    )
    sql = (
        f"INSERT INTO award_nominees ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT (tag_id, year, film_title_primary, person) DO UPDATE SET {updates}"
    )
    conn.execute(sql, tuple(kwargs.values()))
    return conn.execute(
        "SELECT id FROM award_nominees WHERE tag_id=? AND year=? AND film_title_primary=? AND IFNULL(person,'')=IFNULL(?,'')",
        (kwargs["tag_id"], kwargs["year"], kwargs["film_title_primary"], kwargs.get("person")),
    ).fetchone()[0]


def get_award_nominees(
    conn: sqlite3.Connection,
    tag_id: str | None = None,
    org_id: str | None = None,
    year: int | None = None,
    film_id: str | None = None,
    limit: int = 500,
) -> list[dict]:
    sql = "SELECT * FROM award_nominees WHERE 1=1"
    params: list = []
    if tag_id:
        sql += " AND tag_id = ?"
        params.append(tag_id)
    if org_id:
        sql += " AND org_id = ?"
        params.append(org_id)
    if year is not None:
        sql += " AND year = ?"
        params.append(year)
    if film_id:
        sql += " AND matched_film_id = ?"
        params.append(film_id)
    sql += " ORDER BY year DESC, result DESC, created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_ceremony_metrics(conn: sqlite3.Connection) -> dict[tuple[str, int], dict]:
    """Distinct-film counts per (org_id, year) ceremony.

    Editors think in films, not nomination rows. A single film sweeping four
    categories (e.g. 狂野時代 / Busan 2025) was previously inflating the
    "in library" count via row-count summation. This helper returns the
    four metrics needed to render headers like:

        提名 1 / 87 部 · 得獎 1 / 12 部 (片庫)

    Keys:
      nominated_films_total    distinct films appearing anywhere
      won_films_total          distinct films with at least one result='won'
      nominated_films_matched  distinct CATCHPLAY+ films in any nomination
      won_films_matched        distinct CATCHPLAY+ films with at least one win
    """
    rows = conn.execute(
        """
        SELECT
            org_id,
            year,
            COUNT(DISTINCT film_title_primary) AS nominated_films_total,
            COUNT(DISTINCT CASE WHEN result = 'won' THEN film_title_primary END)
                AS won_films_total,
            COUNT(DISTINCT matched_film_id) AS nominated_films_matched,
            COUNT(DISTINCT CASE WHEN result = 'won' THEN matched_film_id END)
                AS won_films_matched
        FROM award_nominees
        GROUP BY org_id, year
        """
    ).fetchall()
    return {
        (r["org_id"], r["year"]): {
            "nominated_films_total": r["nominated_films_total"],
            "won_films_total": r["won_films_total"],
            "nominated_films_matched": r["nominated_films_matched"],
            "won_films_matched": r["won_films_matched"],
        }
        for r in rows
    }


def get_recent_award_batches_v2(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Batches grouped by tag_id + year.

    `total_count` is the number of nomination rows (categories x people).
    `matched_count` is the number of DISTINCT CATCHPLAY+ films matched —
    not the row count — so a single film nominated in 4 categories still
    reads "1 / N 在片庫" instead of "4 / N" which previously confused
    editors who saw four cards of the same film.
    """
    rows = conn.execute(
        """
        SELECT
            tag_id,
            year,
            org_id,
            COUNT(*) AS total_count,
            COUNT(DISTINCT matched_film_id) AS matched_count,
            MAX(created_at) AS latest_insert
        FROM award_nominees
        GROUP BY tag_id, year
        ORDER BY latest_insert DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_tag_reject_stats(conn: sqlite3.Connection, min_reviews: int = 3) -> list[dict]:
    rows = conn.execute(
        """
        SELECT r.tag_id,
               t.dimension,
               t.label_zh_tw,
               COUNT(*) AS total_reviews,
               SUM(CASE WHEN r.action = 'rejected' THEN 1 ELSE 0 END) AS rejected
        FROM tag_reviews r
        LEFT JOIN tags t ON r.tag_id = t.tag_id
        GROUP BY r.tag_id
        HAVING total_reviews >= ?
        ORDER BY (rejected * 1.0 / total_reviews) DESC, total_reviews DESC
        """,
        (min_reviews,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["reject_rate"] = d["rejected"] / d["total_reviews"] if d["total_reviews"] else 0.0
        result.append(d)
    return result
