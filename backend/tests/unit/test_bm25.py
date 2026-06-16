"""Unit tests for BM25 lexical search (jieba + FTS5)."""

import sqlite3

import pytest

from backend.db import init_db
from backend.services import bm25_search as bm


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    films = [
        ("f1", "韓國犯罪驚悚片", "Korean Crime"),
        ("f2", "溫馨家庭喜劇", "Warm Family Comedy"),
        ("f3", "日本恐怖片", "Japanese Horror"),
    ]
    c.executemany("INSERT INTO films(film_id, title_zh, title_en) VALUES (?,?,?)", films)
    c.commit()
    bm._dict_loaded = False  # rebuild dict from this tmp db
    bm.rebuild_fts(c)
    c.commit()
    yield c
    c.close()


def test_segment_splits_cjk():
    assert bm.segment("韓國犯罪") == "韓國 犯罪" or "韓國" in bm.segment("韓國犯罪").split()


def test_bm25_finds_relevant_film(conn):
    hits = bm.bm25_search(conn, "韓國犯罪", top_k=3)
    ids = [f for f, _ in hits]
    assert "f1" in ids
    assert "f3" not in ids  # Japanese horror should not match Korean crime


def test_bm25_empty_query_returns_empty(conn):
    assert bm.bm25_search(conn, "！！！", top_k=3) == []


def test_index_film_adds_runtime_film(conn):
    """A film inserted after the startup rebuild is invisible to BM25 until
    incrementally indexed via index_film (the runtime create/update path)."""
    conn.execute(
        "INSERT INTO films(film_id, title_zh, title_en) VALUES (?,?,?)",
        ("f4", "印度寶萊塢歌舞片", "Bollywood Musical"),
    )
    conn.commit()
    assert "f4" not in [f for f, _ in bm.bm25_search(conn, "寶萊塢", top_k=5)]

    bm.index_film(conn, "f4")
    conn.commit()
    assert "f4" in [f for f, _ in bm.bm25_search(conn, "寶萊塢", top_k=5)]


def test_index_film_is_idempotent(conn):
    """Re-indexing the same film must not duplicate its FTS row (DELETE+INSERT)."""
    bm.index_film(conn, "f1")
    bm.index_film(conn, "f1")
    conn.commit()
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM films_fts WHERE film_id = ?", ("f1",)
    ).fetchone()
    assert count == 1


def test_candidate_ids_restricts_results(conn):
    hits = bm.bm25_search(conn, "片", top_k=5, candidate_ids=["f3"])
    ids = [f for f, _ in hits]
    assert ids in (["f3"], [])
