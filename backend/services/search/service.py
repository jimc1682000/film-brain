"""Search orchestration: hybrid recall → ranking → cached response assembly.

The full business pipeline behind /api/search, callable without HTTP (the
router, warmup, and the eval harness all come through here). No fastapi
imports — errors surface as domain exceptions the router maps to HTTP codes.
"""

from __future__ import annotations

from backend.db import get_db, get_film, get_film_tags
from backend.interfaces import Reranker, VectorStore
from backend.models import SearchRequest, SearchResponse, SearchResult
from backend.services import get_embed_service
from backend.services.hybrid import hybrid_candidates
from backend.services.search.cache import _heavy_cache, _heavy_cache_key
from backend.services.search.planner import _build_query_plan, _labels
from backend.services.search.ranking import (
    _apply_display_scores,
    _apply_weighted_boost,
    _confidence_tier,
    _inject_strong_tag_films,
    _rerank_tags,
)
from backend.services.search_config import get_config
from backend.vector_store import get_film_vector, get_qdrant_client


class FilmVectorNotFoundError(LookupError):
    """No vector indexed for the film — similar-films fallback can't run."""


def _assemble_response(payload: dict, req: SearchRequest) -> SearchResponse:
    """Cheap display tail: slice the cached candidates to top_k, map them into
    the confidence-tier band, build result rows. No embed / recall / cross-
    encoder here — the heavy work already lives in ``payload``."""
    candidates = payload["candidates"]
    understanding = payload["understanding"]
    tier = payload["tier"]
    query_vector = payload["query_vector"]
    requested = payload["requested"]
    cfg = get_config()
    embed = get_embed_service()

    # Honor the per-request floor (excluded from the heavy-cache key so one
    # cached computation serves any min_display_score). The config floor is the
    # honest minimum; a request can only TIGHTEN it, never loosen below it.
    floor = max(cfg["min_display_score"], req.min_display_score)
    # The per-result % is RELATIVE ordering (CE), so its absolute value is
    # meaningless — the band CEILING carries the honest signal: a high-confidence
    # query tops at 95%, an out-of-domain one is capped low so it can't read as a
    # real match (see confidence_tiers in search-config).
    band = cfg["confidence_tiers"][tier]["band"]
    band_lo, band_hi = float(band[0]), float(band[1])
    # Min-max over the FULL candidate pool (above floor), NOT the shown slice, so
    # a given (query, film) shows the SAME % regardless of top_k. Pool is already
    # sorted desc → the shown slice is just its prefix.
    pool = [c for c in candidates if c["display_score"] >= floor]
    vals = [c["display_score"] for c in pool]
    v_lo, v_hi = (min(vals), max(vals)) if vals else (0.0, 0.0)
    v_span = v_hi - v_lo
    shown = pool[: req.top_k]

    results = []
    with get_db() as conn:
        for hit in shown:
            score = hit["display_score"]
            rel = 1.0 if v_span <= 0 else (score - v_lo) / v_span
            matched = _rerank_tags(hit.get("tags", []), query_vector, embed.tag_vector_cache)
            ctags = set(hit.get("tags", []))
            explain = {
                "sources": hit.get("sources", []),
                "matched_prefs": _labels([t for t in requested if t in ctags]),
            }
            # Poster from SQL, not the Qdrant payload (older payloads carry a
            # data: placeholder).
            row = get_film(conn, hit["film_id"])
            results.append(
                SearchResult(
                    film_id=hit["film_id"],
                    title_zh=hit["title_zh"],
                    title_en=hit.get("title_en"),
                    poster_url=row.get("poster_url") if row else hit.get("poster_url"),
                    score=band_lo + rel * (band_hi - band_lo),
                    matched_tags=matched,
                    description_snippet=hit.get("llm_reason", ""),
                    explain=explain,
                )
            )
    return SearchResponse(
        query=req.query, results=results, total=len(results), understanding=understanding
    )


def run_search(req: SearchRequest, reranker: Reranker) -> SearchResponse:
    """Hybrid search: vector + BM25 recall → RRF fusion → optional cross-encoder.

    BM25 (lexical, jieba-segmented) rescues exact / proper-noun matches the
    bi-encoder misses; RRF fuses the two; the cross-encoder does final precision
    ordering when use_llm_rerank is on (slow on CPU, gated behind the flag).
    """
    # Gate phase bypasses the heavy cache: the cache holds full candidate lists
    # (keyed without understand_only), so a warmed query would otherwise return
    # ranked films and skip the gate. understand_only does cheap LLM-only work
    # and never writes the cache, so re-understanding stays light.
    _ckey = _heavy_cache_key(req)
    cached = _heavy_cache.get(_ckey)
    if cached is not None and not req.understand_only:
        return _assemble_response(cached, req)

    embed = get_embed_service()
    cfg = get_config()

    # Query understanding → one weighted tag set + BM25 text + embedded vectors
    # + UI understanding (no recall/rerank yet). The tuned ranking math below is
    # untouched; only the understanding half lives in the planner now.
    plan = _build_query_plan(req, embed)
    requested = plan.requested
    excluded_tags = plan.excluded_tags
    bm25_text = plan.bm25_text
    query_vector = plan.query_vector
    extra_vectors = plan.extra_vectors
    expansion_degraded = plan.expansion_degraded
    understanding = plan.understanding

    # Gate phase: 先聯想，回傳理解讓使用者自評方向，先不 search 片子。沒有候選
    # → 沒有 confidence tier，前端 banner 自動略過。LLM 掛掉（degraded）時前端
    # 會略過 gate 直接走完整搜尋，不叫使用者確認垃圾。
    if req.understand_only:
        return SearchResponse(query=req.query, results=[], total=0, understanding=understanding)

    client = get_qdrant_client()
    pool_size = max(req.rerank_pool, req.top_k) if req.use_llm_rerank else req.top_k

    with get_db() as conn:
        # Pure recall — no hard dimension filter anymore.
        candidates = hybrid_candidates(
            conn,
            client,
            query_text=bm25_text,
            query_vector=query_vector,
            extra_vectors=extra_vectors,
            pool=pool_size,
            filters=None,
        )

        candidates = _inject_strong_tag_films(conn, candidates, requested, excluded_tags, cfg)

        # Out-of-domain gate: best cosine to the USER's query vector (never the
        # HyDE vector — that one is high by construction). Below the calibrated
        # threshold the library simply has no real match and every ranked hit
        # is a semantic guess; flag it so the UI can say so instead of letting
        # the relative top-1 masquerade as a perfect match.
        top_cos = max((c.get("primary_cos", 0.0) for c in candidates), default=0.0)
        tier = _confidence_tier(top_cos, cfg)
        understanding["confidence"] = tier  # high | mid | low → drives banner + ceiling
        understanding["low_confidence"] = tier == "low"  # back-compat for the ⚠ warning

        candidates = _apply_display_scores(candidates, req, reranker)

        candidates = _apply_weighted_boost(candidates, requested, excluded_tags, cfg)

    # Heavy work done. Cache the candidate list + understanding (everything that
    # doesn't depend on top_k / min_display_score), then let the cheap display
    # tail slice + band-rescale it for this request.
    payload = {
        "candidates": candidates,
        "understanding": understanding,
        "tier": tier,
        "query_vector": query_vector,
        "requested": requested,
    }
    # Cache only a CLEAN result: non-empty AND the LLM expansion didn't degrade
    # (rate-limit / error). Caching a degraded response would pin an empty "AI
    # understanding" (no HyDE / keywords) until restart; skipping it lets the
    # query self-heal once the LLM quota recovers.
    if candidates and not expansion_degraded:
        _heavy_cache.set(_ckey, payload)
    return _assemble_response(payload, req)


def similar_films(film_id: str, top_k: int, vector_store: VectorStore) -> SearchResponse:
    """Return precomputed similar films (full BM25+vector→RRF→CE pipeline run
    offline by scripts/05_compute_similar.py).

    The cross-encoder takes 30-40s per film on the CPU demo box, so doing it at
    request time hung the detail page. Precomputed rows make this a cheap lookup.
    Films not yet precomputed (e.g. just imported) fall back to live raw cosine —
    fast but degraded — until the next recompute. Raises FilmVectorNotFoundError
    when the fallback has no vector to search with (router maps it to 404).
    """
    with get_db() as conn:
        film = get_film(conn, film_id)
        # Source film's tags — used to explain WHY a result is similar (the
        # shared tags). Computed live, so it always reflects current tags and
        # needs no recompute of the precomputed table.
        source_tag_ids = {t["tag_id"] for t in get_film_tags(conn, film_id)}
        rows = conn.execute(
            "SELECT similar_film_id, score FROM similar_films "
            "WHERE film_id = ? ORDER BY rank LIMIT ?",
            (film_id, top_k),
        ).fetchall()
        if rows:
            results = []
            for sid, score in rows:
                sf = get_film(conn, sid)
                if not sf:
                    continue
                tag_ids = [t["tag_id"] for t in get_film_tags(conn, sid)]
                shared = [tid for tid in tag_ids if tid in source_tag_ids]
                results.append(
                    SearchResult(
                        film_id=sid,
                        title_zh=sf["title_zh"],
                        title_en=sf.get("title_en"),
                        poster_url=sf.get("poster_url"),
                        score=score,
                        matched_tags=tag_ids[:5],
                        explain={"sources": [], "matched_prefs": _labels(shared)[:5]},
                    )
                )
            title = film["title_zh"] if film else film_id
            return SearchResponse(query=f"相似於：{title}", results=results, total=len(results))

    # Fallback — not precomputed yet: live raw cosine (sub-second, degraded).
    client = get_qdrant_client()
    vector = get_film_vector(client, film_id)
    if not vector:
        raise FilmVectorNotFoundError("Film vector not found. Run embedding generation first.")
    hits = vector_store.search_films(client, vector, top_k=top_k + 1)
    fresh = [h for h in hits if h["film_id"] != film_id][:top_k]
    with get_db() as conn:
        poster_by_id = {
            h["film_id"]: (r.get("poster_url") if (r := get_film(conn, h["film_id"])) else None)
            for h in fresh
        }
        film = get_film(conn, film_id)
        source_tag_ids = {t["tag_id"] for t in get_film_tags(conn, film_id)}
    results = [
        SearchResult(
            film_id=h["film_id"],
            title_zh=h["title_zh"],
            title_en=h.get("title_en"),
            poster_url=poster_by_id.get(h["film_id"]) or h.get("poster_url"),
            score=h["score"],
            matched_tags=h.get("tags", []),
            explain={
                "sources": [],
                "matched_prefs": _labels([t for t in h.get("tags", []) if t in source_tag_ids])[:5],
            },
        )
        for h in fresh
    ]
    title = film["title_zh"] if film else film_id
    return SearchResponse(query=f"相似於：{title}", results=results, total=len(results))
