"""Lexical BM25 search over films via SQLite FTS5 + jieba segmentation.

Vector recall (bge-m3) misses exact keyword / proper-noun matches — a query
for a specific title or a rare term has weak embedding signal and the cosine
band is narrow. BM25 over jieba-segmented text is the complementary lexical
signal; the two recall lists are fused by RRF (see services.fusion) before the
cross-encoder rerank.

CJK has no spaces, so the FTS5 `unicode61` tokenizer would treat a whole title
as one token. We pre-segment with jieba (space-joined) at index + query time so
the tokenizer sees real words. Tag labels are pinned into the jieba dict as
whole words; titles are left to default segmentation (see ensure_dict for why).
"""

from __future__ import annotations

import re
import sqlite3
import threading

import jieba

# Tokens shorter than 2 chars or pure punctuation/symbol add noise to MATCH.
_KEEP = re.compile(r"[\w一-鿿]")
_dict_lock = threading.Lock()
_dict_loaded = False


def ensure_dict(conn: sqlite3.Connection) -> None:
    """Load tag labels into the jieba dict once per process so domain terms
    segment as whole words.

    NB: we deliberately do NOT add full film titles. add_word forces a phrase to
    segment as one token, so a long title would become a single FTS token and a
    query for a constituent word (e.g. searching 犯罪 against 韓國犯罪驚悚片)
    would miss. Titles are left to default segmentation; OR-joined sub-tokens
    still recall them. Tag labels are short curated terms — safe to pin.
    """
    global _dict_loaded
    if _dict_loaded:
        return
    with _dict_lock:
        if _dict_loaded:
            return
        for r in conn.execute("SELECT label_zh_tw FROM tags").fetchall():
            if r[0] and 2 <= len(r[0]) <= 4:
                jieba.add_word(r[0])
        _dict_loaded = True


def segment(text: str) -> str:
    """jieba-segment text into a space-joined token string for FTS5."""
    if not text:
        return ""
    return " ".join(t.strip() for t in jieba.lcut(text) if t.strip())


def _tokens(query: str) -> list[str]:
    return [t for t in (s.strip() for s in jieba.lcut(query)) if len(t) >= 2 and _KEEP.search(t)]


def _match_expr(query: str) -> str:
    """Build an FTS5 MATCH expression from a query. Each token is quoted (so
    FTS5 keywords like OR/NEAR inside a token are treated literally) and the
    tokens are OR-joined for recall."""
    toks = _tokens(query)
    return " OR ".join(f'"{t}"' for t in toks)


def rebuild_fts(conn: sqlite3.Connection) -> int:
    """Wipe + repopulate films_fts from the films table. Idempotent.

    Content per film = title_zh + title_en + description + tmdb_overview +
    its tag labels, jieba-segmented. Returns the row count indexed.
    """
    ensure_dict(conn)
    tagmap: dict[str, list[str]] = {}
    for fid, label in conn.execute(
        "SELECT ft.film_id, t.label_zh_tw FROM film_tags ft JOIN tags t ON ft.tag_id = t.tag_id"
    ).fetchall():
        if label:
            tagmap.setdefault(fid, []).append(label)

    rows = conn.execute(
        "SELECT film_id, title_zh, title_en, description, tmdb_overview FROM films"
    ).fetchall()
    conn.execute("DELETE FROM films_fts")
    n = 0
    for fid, title_zh, title_en, desc, overview in rows:
        raw = " ".join(
            x for x in (title_zh, title_en, desc, overview, " ".join(tagmap.get(fid, []))) if x
        )
        conn.execute("INSERT INTO films_fts(film_id, content) VALUES (?, ?)", (fid, segment(raw)))
        n += 1
    return n


def index_film(conn: sqlite3.Connection, film_id: str) -> None:
    """Incrementally (re)index one film into films_fts.

    For runtime create/update flows — rebuild_fts only runs at startup, so a
    film added while the backend is live would otherwise miss BM25 recall until
    a restart. DELETE-then-INSERT makes this idempotent (also covers updates).
    Content mirrors rebuild_fts: titles + description + overview + tag labels.
    """
    ensure_dict(conn)
    labels = [
        label
        for (label,) in conn.execute(
            "SELECT t.label_zh_tw FROM film_tags ft JOIN tags t ON ft.tag_id = t.tag_id "
            "WHERE ft.film_id = ?",
            (film_id,),
        ).fetchall()
        if label
    ]
    row = conn.execute(
        "SELECT title_zh, title_en, description, tmdb_overview FROM films WHERE film_id = ?",
        (film_id,),
    ).fetchone()
    if row is None:
        return
    raw = " ".join(x for x in (*row, " ".join(labels)) if x)
    conn.execute("DELETE FROM films_fts WHERE film_id = ?", (film_id,))
    conn.execute("INSERT INTO films_fts(film_id, content) VALUES (?, ?)", (film_id, segment(raw)))


def bm25_search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 40,
    candidate_ids: list[str] | None = None,
) -> list[tuple[str, float]]:
    """Return [(film_id, bm25_score)] ranked best-first.

    SQLite `bm25()` returns a value where more-negative = better; ORDER BY it
    ascending yields best matches first. `candidate_ids` restricts the search to
    a pre-filtered set (used to honour hard dimension filters that the FTS index
    itself can't express).
    """
    ensure_dict(conn)
    expr = _match_expr(query)
    if not expr:
        return []
    sql = "SELECT film_id, bm25(films_fts) AS score FROM films_fts WHERE films_fts MATCH ?"
    params: list = [expr]
    if candidate_ids is not None:
        if not candidate_ids:
            return []
        placeholders = ",".join("?" * len(candidate_ids))
        sql += f" AND film_id IN ({placeholders})"
        params.extend(candidate_ids)
    sql += " ORDER BY bm25(films_fts) LIMIT ?"
    params.append(top_k)
    return [(r[0], r[1]) for r in conn.execute(sql, params).fetchall()]
