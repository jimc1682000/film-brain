"""Integration tests for hybrid recall (Qdrant search faked, BM25 + SQL real).

A fake `VectorStore` is injected (ADR 0021 seam) to return deterministic hits so
we exercise the fusion + hydration pipeline without Qdrant; BM25 runs for real
against a seeded in-memory FTS index.
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.db import init_db
from backend.services import hybrid
from backend.services.bm25_search import rebuild_fts
from backend.tests.fixtures.mock_films import fake_embed, seed_mock_db


@pytest.fixture
def seeded_conn(tmp_path):
    """A seeded SQLite conn with the FTS index rebuilt."""
    db = tmp_path / "hybrid.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    seed_mock_db(conn)
    rebuild_fts(conn)
    conn.commit()
    yield conn
    conn.close()


def _fake_hit(film_id: str, score: float) -> dict:
    """Build a Qdrant-style hit dict mirroring vector_store.search_films output."""
    return {
        "film_id": film_id,
        "title_zh": film_id,
        "title_en": film_id.upper(),
        "tags": ["genre"],
        "score": score,
        "poster_url": f"https://example.com/{film_id}.jpg",
    }


class _FakeVectorStore:
    """VectorStore Protocol double returning fixed hits; honours top_k."""

    def __init__(self, hits: list[dict]):
        self._hits = hits

    def search_films(self, client, query_vector, top_k=10, dimension_filters=None):
        assert isinstance(top_k, int)
        # honour top_k so the recall+1 slicing path is exercised
        return self._hits[:top_k]


# ── films_matching_filters (pure SQL) ───────────────────────────────────────


def test_films_matching_filters_none_when_no_filters(seeded_conn):
    assert hybrid.films_matching_filters(seeded_conn, None) is None
    assert hybrid.films_matching_filters(seeded_conn, {}) is None


def test_films_matching_filters_empty_values_returns_none(seeded_conn):
    # A dim present but with an empty value list contributes no set.
    assert hybrid.films_matching_filters(seeded_conn, {"genre": []}) is None


def test_films_matching_filters_single_dim(seeded_conn):
    out = hybrid.films_matching_filters(seeded_conn, {"genre": ["comedy"]})
    assert set(out) == {"mock-001", "mock-009"}


def test_films_matching_filters_intersection_across_dims(seeded_conn):
    # AND across dims: only films carrying BOTH tags survive.
    out = hybrid.films_matching_filters(seeded_conn, {"d1": ["comedy"], "d2": ["romance"]})
    assert set(out) == {"mock-009"}


# ── hybrid_candidates ───────────────────────────────────────────────────────


def test_hybrid_candidates_basic(seeded_conn):
    hits = [_fake_hit("mock-004", 0.9), _fake_hit("mock-010", 0.7)]
    qvec = fake_embed(["太空科幻"])[0]
    out = hybrid.hybrid_candidates(
        seeded_conn,
        client=None,
        query_text="科幻",
        query_vector=qvec,
        vector_store=_FakeVectorStore(hits),
    )
    assert out
    ids = {c["film_id"] for c in out}
    assert "mock-004" in ids
    top = out[0]
    assert "rrf_score" in top and "primary_cos" in top and "sources" in top
    # vector-sourced hit carries its cosine
    by_id = {c["film_id"]: c for c in out}
    assert by_id["mock-004"]["primary_cos"] == 0.9
    assert "vector" in by_id["mock-004"]["sources"]


def test_hybrid_candidates_bm25_only_hydration(seeded_conn):
    # Vector recall returns ONLY mock-004, but BM25 will surface 喜劇 films
    # (mock-001 / mock-009) that vector never saw → forces the get_film
    # hydration branch (vmap.get(fid) is None).
    qvec = fake_embed(["x"])[0]
    out = hybrid.hybrid_candidates(
        seeded_conn,
        client=None,
        query_text="喜劇",
        query_vector=qvec,
        vector_store=_FakeVectorStore([_fake_hit("mock-004", 0.8)]),
    )
    by_id = {c["film_id"]: c for c in out}
    # a BM25-only film must be present and hydrated from SQL
    bm_only = [c for c in out if "bm25" in c["sources"] and "vector" not in c["sources"]]
    assert bm_only, "expected at least one BM25-only hydrated candidate"
    hydrated = bm_only[0]
    assert hydrated["title_zh"]  # came from get_film
    assert hydrated["score"] == 0.0  # hydration default
    assert isinstance(hydrated["tags"], list)
    assert hydrated["primary_cos"] == 0.0
    assert "mock-004" in by_id


def test_hybrid_candidates_with_hyde_and_exclude(seeded_conn):
    # extra_vectors → HyDE branch; exclude_id drops a film from every list.
    hits = [_fake_hit("mock-004", 0.9), _fake_hit("mock-010", 0.6)]
    qvec = fake_embed(["a"])[0]
    hyde = fake_embed(["b"])[0]
    out = hybrid.hybrid_candidates(
        seeded_conn,
        client=None,
        query_text="科幻",
        query_vector=qvec,
        extra_vectors=[hyde],
        exclude_id="mock-004",
        vector_store=_FakeVectorStore(hits),
    )
    ids = {c["film_id"] for c in out}
    assert "mock-004" not in ids  # excluded everywhere
    by_id = {c["film_id"]: c for c in out}
    # mock-010 came from both primary + hyde vector lists → hyde source tagged
    assert "hyde" in by_id["mock-010"]["sources"]


def test_hybrid_candidates_with_filters_and_pool(seeded_conn):
    # filters drive films_matching_filters + candidate_ids into BM25.
    qvec = fake_embed(["c"])[0]
    out = hybrid.hybrid_candidates(
        seeded_conn,
        client=None,
        query_text="喜劇",
        query_vector=qvec,
        filters={"genre": ["comedy"]},
        pool=2,
        vector_store=_FakeVectorStore([_fake_hit("mock-001", 0.5)]),
    )
    assert len(out) <= 2
    # BM25 was constrained to comedy films
    bm_ids = {c["film_id"] for c in out if "bm25" in c["sources"]}
    assert bm_ids <= {"mock-001", "mock-009"}
