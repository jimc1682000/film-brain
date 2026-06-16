"""Real-Qdrant wiring smoke — the gate the mocked suite can't give.

Every other test monkeypatches Qdrant, so a broken vector_store ↔ Qdrant ↔
hybrid path would still go green. This exercises the real client against a live
Qdrant (the CI `integration` job's service container) using deterministic
`fake_embed` vectors — no embedding model, no live HTTP server, ~seconds.

Skipped automatically when no Qdrant is reachable (e.g. the mocked `test` job or
a dev box without Docker), so it never blocks the normal suite.
"""

from __future__ import annotations

import contextlib
import sqlite3

import pytest

from backend import vector_store as vs
from backend.config import settings
from backend.db import init_db
from backend.services.bm25_search import rebuild_fts
from backend.services.hybrid import hybrid_candidates
from backend.tests.fixtures.mock_films import MOCK_FILMS, fake_embed, seed_mock_db


def _qdrant_reachable() -> bool:
    try:
        vs.get_qdrant_client().get_collections()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.qdrant,
    pytest.mark.skipif(not _qdrant_reachable(), reason="needs a running Qdrant"),
]


@pytest.fixture
def live_qdrant(monkeypatch):
    """A throwaway collection seeded with mock films (fake_embed vectors)."""
    monkeypatch.setattr(settings, "qdrant_collection", "ci_roundtrip", raising=False)
    client = vs.get_qdrant_client()
    # Start clean even if a prior run died mid-way.
    with contextlib.suppress(Exception):
        client.delete_collection(settings.qdrant_collection)
    vs.ensure_collection(client)
    for film in MOCK_FILMS:
        payload = vs.build_film_payload(
            {
                "film_id": film["film_id"],
                "title_zh": film["title_zh"],
                "title_en": film["title_en"],
                "poster_url": None,
            },
            [{"tag_id": t, "dimension": "genre"} for t in film["tags"]],
        )
        vec = fake_embed([film["title_zh"]])[0]
        vs.upsert_film_vector(client, film["film_id"], vec, payload)
    yield client
    with contextlib.suppress(Exception):
        client.delete_collection(settings.qdrant_collection)


def test_search_films_roundtrips(live_qdrant):
    """upsert → search_films returns the seeded films with payload fields."""
    qvec = fake_embed([MOCK_FILMS[0]["title_zh"]])[0]
    hits = vs.search_films(live_qdrant, qvec, top_k=5)
    assert hits, "real Qdrant search returned nothing"
    top = hits[0]
    assert top["film_id"] and "title_zh" in top and "score" in top
    # The exact-title query vector should rank its own film first.
    assert top["film_id"] == MOCK_FILMS[0]["film_id"]


def test_dimension_filter_roundtrips(live_qdrant):
    """Payload dim_* arrays drive a real Qdrant filtered search."""
    qvec = fake_embed(["comedy"])[0]
    hits = vs.search_films(live_qdrant, qvec, top_k=10, dimension_filters={"genre": ["comedy"]})
    ids = {h["film_id"] for h in hits}
    # Only films carrying the comedy tag may come back.
    comedy = {f["film_id"] for f in MOCK_FILMS if "comedy" in f["tags"]}
    assert ids and ids.issubset(comedy)


def test_hybrid_candidates_roundtrips(live_qdrant, tmp_path):
    """Full hybrid path (vector + BM25 → RRF) against real Qdrant + real SQLite."""
    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    seed_mock_db(conn)
    rebuild_fts(conn)
    conn.commit()

    qvec = fake_embed(["喜劇"])[0]
    out = hybrid_candidates(conn, live_qdrant, query_text="喜劇", query_vector=qvec)
    assert out, "hybrid returned no candidates against real Qdrant"
    top = out[0]
    assert "rrf_score" in top and "sources" in top
    conn.close()
