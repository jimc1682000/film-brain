"""Hybrid recall: vector + BM25 → RRF fusion → candidate dicts.

Shared by the live search endpoint and the offline similar-films precompute so
both rank with the same pipeline. The caller applies the cross-encoder rerank
on the returned candidates (kept separate because CE is the expensive step and
some paths want to gate or skip it).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from backend.db import get_film, get_film_tags
from backend.services.bm25_search import bm25_search
from backend.services.fusion import rrf_fuse
from backend.services.search_config import get_config
from backend.vector_store import get_vector_store

if TYPE_CHECKING:
    from backend.interfaces import VectorStore


def films_matching_filters(
    conn: sqlite3.Connection, filters: dict[str, list[str]] | None
) -> list[str] | None:
    """Film ids satisfying every dimension filter (AND across dims, OR within).

    Used to constrain BM25 — the FTS index can't express dimension filters, so
    we pass the allowed set explicitly to keep lexical recall consistent with
    the hard filters the vector side enforces via the Qdrant payload.
    """
    if not filters:
        return None
    sets: list[set[str]] = []
    for values in filters.values():
        if not values:
            continue
        placeholders = ",".join("?" * len(values))
        rows = conn.execute(
            f"SELECT DISTINCT film_id FROM film_tags WHERE tag_id IN ({placeholders})", values
        ).fetchall()
        sets.append({r[0] for r in rows})
    if not sets:
        return None
    allowed = set.intersection(*sets)
    return list(allowed)


def _provenance(fid: str, vec_set: set, hyde_set: set, bm_set: set) -> list[str]:
    """Which recall paths surfaced this film (explainability)."""
    sources = []
    if fid in vec_set:
        sources.append("vector")
    if fid in hyde_set:
        sources.append("hyde")
    if fid in bm_set:
        sources.append("bm25")
    return sources


def hybrid_candidates(
    conn: sqlite3.Connection,
    client,
    *,
    query_text: str,
    query_vector: list[float],
    extra_vectors: list[list[float]] | None = None,
    pool: int | None = None,
    filters: dict[str, list[str]] | None = None,
    exclude_id: str | None = None,
    vector_store: VectorStore | None = None,
) -> list[dict]:
    """Return fused candidate dicts (best-first, pre-rerank).

    Multi-recall: the primary query vector plus any `extra_vectors` (e.g. a
    HyDE-text embedding) each run a vector search; BM25 runs on `query_text`
    (optionally keyword-augmented). All ranked lists are RRF-fused. Weights /
    recall / pool / RRF-k all come from the hot-reloaded search-config.json.

    Each dict carries film_id / title_zh / title_en / tags / poster_url, the
    raw vector `score`, and the fused `rrf_score`.
    """
    cfg = get_config()
    vs = vector_store or get_vector_store()
    recall = cfg["recall"]
    pool = pool if pool is not None else cfg["pool"]
    w = cfg["weights"]

    vector_lists: list[list[str]] = []
    weights: list[float] = []
    vmap: dict[str, dict] = {}
    # Cosine to the USER's query vector only (not HyDE). vmap's `score` is
    # whichever vector list inserted the film first, so a HyDE-only hit would
    # carry a misleadingly-high cosine to the hallucinated plot text. This map
    # is the honest absolute-relevance signal for the low-confidence gate.
    primary_cos: dict[str, float] = {}
    all_vectors: list[list[float]] = [query_vector, *(extra_vectors or [])]
    for i, vec in enumerate(all_vectors):
        hits = vs.search_films(client, vec, top_k=recall + 1, dimension_filters=filters or None)
        if exclude_id:
            hits = [h for h in hits if h["film_id"] != exclude_id]
        vector_lists.append([h["film_id"] for h in hits])
        # Primary vector outranks the HyDE vector; both outrank BM25.
        weights.append(w["vector"] if i == 0 else w["hyde"])
        if i == 0:
            primary_cos = {h["film_id"]: h["score"] for h in hits}
        for h in hits:
            vmap.setdefault(h["film_id"], h)

    candidate_ids = films_matching_filters(conn, filters)
    bm = bm25_search(conn, query_text, top_k=recall, candidate_ids=candidate_ids)
    bids = [f for f, _ in bm if f != exclude_id]

    fused = rrf_fuse(
        [*vector_lists, bids],
        k=cfg["rrf_k"],
        weights=[*weights, w["bm25"]],
        top_bonus=tuple(cfg["top_bonus"]),
    )[:pool]

    # Provenance for explainability: which recall paths surfaced each film.
    vec_set = set(vector_lists[0]) if vector_lists else set()
    hyde_set = set().union(*vector_lists[1:]) if len(vector_lists) > 1 else set()
    bm_set = set(bids)

    out: list[dict] = []
    for fid, rrf in fused:
        hit = vmap.get(fid)
        if hit is None:  # BM25-only — hydrate from SQL
            row = get_film(conn, fid)
            if not row:
                continue
            hit = {
                "film_id": fid,
                "title_zh": row["title_zh"],
                "title_en": row.get("title_en"),
                "tags": [t["tag_id"] for t in get_film_tags(conn, fid)],
                "score": 0.0,
                "poster_url": row.get("poster_url"),
            }
        merged = dict(hit)
        merged["rrf_score"] = rrf
        merged["primary_cos"] = primary_cos.get(fid, 0.0)
        merged["sources"] = _provenance(fid, vec_set, hyde_set, bm_set)
        out.append(merged)
    return out
