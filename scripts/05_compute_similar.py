"""Precompute similar films per film via the full hybrid pipeline.

For each film: BM25 + vector recall → RRF fusion → cross-encoder rerank, using
the film's own text + vector as the query, then write the top-N into the
similar_films table. The cross-encoder is slow on CPU (~30-40s/film), so this
runs offline — the API serves a cheap table lookup afterwards.

Run where Qdrant + embeddings are available (i.e. the VPS, or a box with the
vector store populated). After running on the VPS, `make pull-db` brings the
similar_films rows into the repo DB to commit.

Usage:
    python -m scripts.05_compute_similar [--top-n N] [--pool P] [--limit L]
"""

from __future__ import annotations

import argparse
import time

from backend.db import get_db, get_film_tags
from backend.services.bm25_search import rebuild_fts
from backend.services.hybrid import hybrid_candidates
from backend.services.reranker import rerank_with_cross_encoder
from backend.vector_store import get_film_vector, get_qdrant_client


def _query_text(conn, film: dict) -> str:
    tags = [t["label_zh_tw"] for t in get_film_tags(conn, film["film_id"])]
    parts = [
        film.get("title_zh"),
        film.get("title_en"),
        (film.get("description") or film.get("tmdb_overview") or "")[:300],
        ("標籤: " + ", ".join(tags)) if tags else None,
    ]
    return " ".join(p for p in parts if p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=10, help="similar films stored per film")
    ap.add_argument("--pool", type=int, default=30, help="candidates fed to the reranker")
    ap.add_argument("--limit", type=int, default=0, help="only process first N films (0 = all)")
    args = ap.parse_args()

    client = get_qdrant_client()
    with get_db() as conn:
        print("[similar] rebuilding BM25 index...", flush=True)
        rebuild_fts(conn)
        films = [
            dict(r)
            for r in conn.execute(
                "SELECT film_id, title_zh, title_en, description, tmdb_overview FROM films"
            ).fetchall()
        ]
        if args.limit:
            films = films[: args.limit]
        total = len(films)
        conn.execute("DELETE FROM similar_films")

        start = time.time()
        written = 0
        for i, film in enumerate(films, 1):
            fid = film["film_id"]
            vec = get_film_vector(client, fid)
            if not vec:
                continue
            qtext = _query_text(conn, film)
            cands = hybrid_candidates(
                conn, client, query_text=qtext, query_vector=vec, pool=args.pool, exclude_id=fid
            )
            if not cands:
                continue
            ranked = rerank_with_cross_encoder(qtext, cands) or cands
            for rank, c in enumerate(ranked[: args.top_n]):
                score = c.get("llm_score", c.get("rrf_score", 0.0))
                conn.execute(
                    "INSERT OR REPLACE INTO similar_films(film_id, similar_film_id, rank, score) "
                    "VALUES (?, ?, ?, ?)",
                    (fid, c["film_id"], rank, float(score)),
                )
            written += 1
            if i % 20 == 0 or i == total:
                conn.commit()
                elapsed = time.time() - start
                rate = elapsed / i
                print(
                    f"[similar] {i}/{total}  {elapsed:.0f}s  ~{rate:.1f}s/film  "
                    f"ETA {(total - i) * rate / 60:.0f}min",
                    flush=True,
                )
        conn.commit()
    print(f"[similar] done — {written} films precomputed", flush=True)


if __name__ == "__main__":
    main()
