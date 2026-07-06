import math
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException

from backend.config import settings
from backend.db import get_db, get_film, get_film_tags
from backend.interfaces import Reranker, VectorStore
from backend.models import SearchRequest, SearchResponse, SearchResult
from backend.ratelimit import rate_limit_search
from backend.services import get_embed_service
from backend.services.hybrid import hybrid_candidates
from backend.services.pinned_lru import PinnedLRU
from backend.services.query_expand import expand_query
from backend.services.reranker import get_reranker
from backend.services.search_config import boost_weight, get_config
from backend.tag_registry import TagRegistry
from backend.vector_store import get_film_vector, get_qdrant_client, get_vector_store

router = APIRouter()

_award_tag_ids: set[str] | None = None


def _get_award_tag_ids() -> set[str]:
    """Cache of tag_ids whose dimension marks them as an actual award nomination
    or curation entry. Used as the post-filter set when the query parser flags
    award presence as a hard requirement."""
    global _award_tag_ids
    if _award_tag_ids is None:
        reg = TagRegistry()
        ids: set[str] = set()
        for dim in ("award", "curation-award"):
            for tag in reg.get_tags_by_dimension(dim):
                ids.add(tag["tag_id"])
        _award_tag_ids = ids
    return _award_tag_ids


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-9)


def _rerank_tags(
    film_tags: list[str], query_vector: list[float], cache: dict[str, list[float]], top_n: int = 5
) -> list[str]:
    """Return top_n tag_ids from film.tags ranked by cosine(query, tag_label_vec).

    Falls back to original order if cache is empty (e.g. warmup failed).
    """
    if not cache:
        return film_tags[:top_n]
    scored = [(tid, _cosine(query_vector, cache[tid])) for tid in film_tags if tid in cache]
    scored.sort(key=lambda x: -x[1])
    matched = [tid for tid, _ in scored[:top_n]]
    # If some tags weren't in cache, backfill to reach top_n
    if len(matched) < top_n:
        for tid in film_tags:
            if tid not in matched and len(matched) < top_n:
                matched.append(tid)
    return matched


def _minmax(values: list[float]) -> list[float]:
    """Scale to 0-1 for display. RRF scores are tiny (~0.01-0.03); without the
    cross-encoder the user-visible % would otherwise be meaningless."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo if hi > lo else 1.0
    return [(v - lo) / span for v in values]


_registry_cache: TagRegistry | None = None


def _labels(tag_ids: list[str]) -> list[str]:
    """zh_TW labels for tag_ids (for the explainability UI)."""
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = TagRegistry()
    out = []
    for t in tag_ids:
        tag = _registry_cache.get_tag(t)
        out.append((tag.get("labels", {}).get("zh_TW") if tag else None) or t)
    return out


def _excluded_tag_ids(terms: list[str]) -> set[str]:
    """Resolve user-excluded labels (gate ✕) → tag_ids. Labels aren't unique
    across dimensions → take every match. Unknown labels (an LLM keyword, not a
    taxonomy tag) resolve to nothing here; they're still stripped from the BM25
    keywords separately."""
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = TagRegistry()
    out: set[str] = set()
    for term in terms:
        out.update(_registry_cache.get_tag_ids_by_label(term))
    return out


def _tag_signals(requested: dict[str, float]) -> list[dict]:
    """Structured view of the weighted tag set for the UI: tag_id + zh_TW label
    + dimension + weight. Lets the gate reference tags by id (not just label)."""
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = TagRegistry()
    out = []
    for tid, w in requested.items():
        tag = _registry_cache.get_tag(tid)
        out.append(
            {
                "tag_id": tid,
                "label": (tag.get("labels", {}).get("zh_TW") if tag else None) or tid,
                "dim": tag.get("dimension") if tag else None,
                "weight": w,
            }
        )
    return out


# In-process cache of the EXPENSIVE search output (post-rerank, post-boost
# candidate list + query understanding), keyed by the knobs that affect it.
# Deliberately EXCLUDES top_k and min_display_score: those only drive the cheap
# display tail (slice + per-slice band rescale), which runs live per request —
# so one cached computation serves top_k 5/10/20 with correct scores (naive
# slicing of a top-20 response would mis-scale the % because the band is
# re-min-maxed over the shown slice). Lets startup warm the demo chips so a
# click skips the ~7s CPU cross-encoder; cleared on restart, capped for memory.
_heavy_cache = PinnedLRU(64)


def _heavy_cache_key(req: SearchRequest) -> tuple:
    filters = tuple(sorted((d, tuple(sorted(v))) for d, v in (req.dimension_filters or {}).items()))
    return (
        req.query.strip(),
        req.use_llm_rerank,
        req.rerank_pool,
        filters,
        tuple(sorted(e.strip() for e in (req.exclude or []) if e and e.strip())),
    )


def pin_demo_query(req: SearchRequest) -> bool:
    """Pin a warmed demo query's heavy-cache entry so reloop-generated user
    queries can never evict it. Same key the request would hit on a chip click."""
    return _heavy_cache.pin(_heavy_cache_key(req))


_TIER_ORDER = ("high", "mid", "low")


def _confidence_tier(top_cos: float, cfg: dict) -> str:
    """Pick the confidence tier from the best query-vector cosine. Cosine is the
    only signal that separates a real match from an out-of-domain guess — the CE
    score does NOT (it rates an unrelated film as highly as a real hit) — so the
    tier, and thus the display ceiling + banner, keys off it."""
    tiers = cfg["confidence_tiers"]
    for name in _TIER_ORDER:  # high → mid → low; first whose cosine floor we clear
        if top_cos >= tiers[name]["min_cos"]:
            return name
    return "low"


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


@dataclass
class QueryPlan:
    """Everything the query-understanding half produces, consumed by the ranking
    half. Pure data — building it does NO recall/rerank. Keeps the understanding
    logic in one place; the tuned ranking math stays in semantic_search."""

    requested: dict[str, float]  # tag_id -> positive boost weight
    excluded_tags: set[str]  # tag_ids the user excluded (negative)
    bm25_text: str  # positive query + kept keywords (excluded stripped)
    query_vector: list[float]
    extra_vectors: list | None  # HyDE / step-back vectors
    understanding: dict  # the UI's "how I read you" payload
    expansion_degraded: bool  # LLM expansion failed → don't cache
    require_award: bool = False


def _apply_query_expansion(req: SearchRequest, requested: dict[str, float], add) -> dict:
    """LLM query-understanding phase (the SOLE understanding path): map the query
    to taxonomy tags (soft boosts, written into `requested` via `add`), an
    award-presence flag, HyDE / step-back texts, and BM25 keywords. Returns the
    non-tag outputs; tag boosts land in `requested`.

    One LLM call replaces the old regex keyword parser — its hand-maintained
    bilingual lexicon did a brittle subset of what the LLM already does.
    """
    exp = expand_query(req.query, timeout=settings.query_expansion_timeout)
    expansion_degraded = bool(exp.get("_degraded"))
    for dim, values in exp["filters"].items():
        for t in values:
            add(t, dim)
    for tid, w in exp.get("boost_tags", []):
        requested[tid] = max(requested.get(tid, 0.0), float(w))
    # Generic "award-winning films" intent (no specific ceremony) → inject the
    # whole award dimension as boosts; specific ceremony tags arrive via exp's tags.
    require_award = bool(exp.get("award_presence"))
    if require_award:
        for t in _get_award_tag_ids():
            add(t, "award")
    # Specificity gate for step-back: the query is "specific" if the LLM mapped it
    # to any tag / award intent, or the user gave explicit filters. The abstracted
    # step-back vector helps vague vibe queries but injects noise into specific
    # ones (v5 ran it always-on and lost 6 specific to gain 2 vague).
    specific = bool(
        exp["filters"] or exp.get("boost_tags") or require_award or req.dimension_filters
    )
    # HyDE: hypothetical plot for the original query (always on, anchored to the
    # user's words). Step-back: abstracted rephrasing — gated on !specific.
    extra_texts: list[str] = []
    hyde_text = ""
    if exp["hyde_text"]:
        hyde_text = exp["hyde_text"]
        extra_texts.append(hyde_text)
    if exp.get("stepback_text") and not specific:
        extra_texts.append(exp["stepback_text"])
    bm25_text = req.query
    used_keywords: list[str] = []
    if exp["keywords"]:
        bm25_text = req.query + " " + " ".join(exp["keywords"])
        used_keywords = exp["keywords"]
    return {
        "require_award": require_award,
        "bm25_text": bm25_text,
        "used_keywords": used_keywords,
        "hyde_text": hyde_text,
        "expansion_degraded": expansion_degraded,
        "extra_texts": extra_texts,
    }


def _build_query_plan(req: SearchRequest, embed) -> QueryPlan:
    """Turn the raw query (+ structured exclude) into a QueryPlan: one weighted
    tag set (parser + LLM expansion, all soft), the BM25 text, the embedded
    vectors, and the UI understanding. No hard filters — every signal is a
    weight. Exclusions are structured (not folded into the query), so the
    embedded text + BM25 base stay positive."""
    requested: dict[str, float] = {}  # tag_id -> weight

    def _add(tag_id: str, dim: str) -> None:
        w = boost_weight(dim)
        if w > 0:
            requested[tag_id] = max(requested.get(tag_id, 0.0), w)

    # User-supplied explicit filters (from the UI) — always honoured as boosts.
    for dim, values in (req.dimension_filters or {}).items():
        for t in values:
            _add(t, dim)

    require_award = False
    bm25_text = req.query
    used_keywords: list[str] = []
    hyde_text = ""
    expansion_degraded = False
    extra_texts: list[str] = []  # HyDE / step-back texts, embedded with the query
    if settings.use_query_expansion:
        exp_out = _apply_query_expansion(req, requested, _add)
        require_award = exp_out["require_award"]
        bm25_text = exp_out["bm25_text"]
        used_keywords = exp_out["used_keywords"]
        hyde_text = exp_out["hyde_text"]
        expansion_degraded = exp_out["expansion_degraded"]
        extra_texts = exp_out["extra_texts"]

    # User exclusions (gate ✕). Structured — NOT folded into req.query so the
    # embedded text + BM25 base stay positive (dense recall ignores negation; a
    # folded 不要X would still pull X). Resolve labels → tag_ids, drop from the
    # positive boost set and the BM25 keywords. The score penalty is applied in
    # the ranking boost loop; strong-inject skips excluded-tag films.
    excluded_terms = [e.strip() for e in (req.exclude or []) if e and e.strip()]
    excluded_tags = _excluded_tag_ids(excluded_terms) if excluded_terms else set()
    for tid in excluded_tags:
        requested.pop(tid, None)
    if excluded_terms:
        used_keywords = [k for k in used_keywords if k not in excluded_terms]
        bm25_text = req.query + (" " + " ".join(used_keywords) if used_keywords else "")

    # ONE bge-m3 call for query + HyDE + step-back (same model), then split.
    _vecs = embed.embed([req.query, *extra_texts])
    query_vector = _vecs[0]
    extra_vectors = _vecs[1:] or None

    understanding = {
        "filters": _labels(list(requested)),
        # Structured tag signals (tag_id + dim + weight) alongside the flat
        # label list — lets the gate reference tags by id, not just label.
        "tags": _tag_signals(requested),
        "keywords": used_keywords,
        "award_required": require_award,
        # Surface the HyDE plot so a pure-semantic hit is explainable in the UI.
        "hyde_text": hyde_text,
        # LLM query-expansion failed → the UI says so honestly + explains the
        # keyword/vector fallback instead of an empty "how I read you" box.
        "degraded": expansion_degraded,
        # User-excluded directions (gate ✕) — already removed from filters/keywords.
        "excluded": excluded_terms,
    }

    return QueryPlan(
        requested=requested,
        excluded_tags=excluded_tags,
        bm25_text=bm25_text,
        query_vector=query_vector,
        extra_vectors=extra_vectors,
        understanding=understanding,
        expansion_degraded=expansion_degraded,
        require_award=require_award,
    )


def _inject_strong_tag_films(conn, candidates: list[dict], requested, excluded_tags, cfg):
    """Inject films carrying STRONG requested tags (weight ≥ threshold) into the
    pool so a high-signal intent (得獎 / 地區) always has results to rank, rather
    than depending on whether recall surfaced them. Skips films the user excluded
    a direction from. Returns the (possibly extended) candidate list."""
    strong = [t for t, w in requested.items() if w >= cfg["inject_weight_threshold"]]
    if not strong:
        return candidates
    present = {c["film_id"] for c in candidates}
    ph = ",".join("?" * len(strong))
    for (fid,) in conn.execute(
        f"SELECT DISTINCT film_id FROM film_tags WHERE tag_id IN ({ph})", strong
    ).fetchall():
        if fid in present:
            continue
        row = get_film(conn, fid)
        if not row:
            continue
        fid_tags = [t["tag_id"] for t in get_film_tags(conn, fid)]
        # Don't force-inject a film the user excluded a direction from — injecting
        # then penalising it is wasted work (and risks it surviving). Skip outright.
        if excluded_tags and excluded_tags.intersection(fid_tags):
            continue
        candidates.append(
            {
                "film_id": fid,
                "title_zh": row["title_zh"],
                "title_en": row.get("title_en"),
                "tags": fid_tags,
                "score": 0.0,
                "rrf_score": 0.0,
                # Distinct provenance: not recalled, injected because it carries a
                # strong requested tag. UI renders 符合條件 (vs 共同 on similar).
                "sources": ["inject"],
            }
        )
    return candidates


def _apply_display_scores(candidates: list[dict], req: SearchRequest, reranker: Reranker):
    """Base display score: CE precision rerank when requested (position-aware
    blend against retrieval order), else RRF min-max normalised. Returns the
    (possibly reranked) candidate list."""
    if req.use_llm_rerank and candidates:
        # Stamp pre-CE rank so the cross-encoder can blend against retrieval order.
        for i, c in enumerate(candidates):
            c["_pre_ce_rank"] = i
        reranked = reranker.rerank(req.query, candidates)
        if reranked is not None:
            candidates = reranked
            for c in candidates:
                c["display_score"] = c.get("llm_score", 0.0)
    if not (req.use_llm_rerank and candidates and "display_score" in candidates[0]):
        norm = _minmax([c["rrf_score"] for c in candidates])
        for c, s in zip(candidates, norm, strict=False):
            c["display_score"] = s
    return candidates


def _apply_weighted_boost(candidates: list[dict], requested, excluded_tags, cfg):
    """Unified weighted boost: a film gains scale * Σ(weight of requested tags it
    carries). Excluded directions (gate ✕) subtract a large penalty per excluded
    tag so the film drops below the display floor — same soft channel as boost,
    so an all-excluded pool yields an honest empty list, not a crash. Re-sorts."""
    if not (requested or excluded_tags):
        return candidates
    scale = cfg["tag_boost_scale"]
    penalty = cfg["exclude_penalty"]
    for c in candidates:
        ctags = set(c.get("tags", []))
        bonus = sum(w for t, w in requested.items() if t in ctags)
        if bonus:
            c["display_score"] = c["display_score"] + scale * bonus
        if excluded_tags:
            hit = excluded_tags.intersection(ctags)
            if hit:
                c["display_score"] = c["display_score"] - penalty * len(hit)
    candidates.sort(key=lambda c: -c["display_score"])
    return candidates


@router.post("/", response_model=SearchResponse, dependencies=[Depends(rate_limit_search)])
async def semantic_search(req: SearchRequest, reranker: Reranker = Depends(get_reranker)):
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
    # untouched; only the understanding half lives in the helper now.
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


@router.get(
    "/similar/{film_id}",
    response_model=SearchResponse,
    dependencies=[Depends(rate_limit_search)],
)
async def similar_films(
    film_id: str, top_k: int = 5, vector_store: VectorStore = Depends(get_vector_store)
):
    """Return precomputed similar films (full BM25+vector→RRF→CE pipeline run
    offline by scripts/05_compute_similar.py).

    The cross-encoder takes 30-40s per film on the CPU demo box, so doing it at
    request time hung the detail page. Precomputed rows make this a cheap lookup.
    Films not yet precomputed (e.g. just imported) fall back to live raw cosine —
    fast but degraded — until the next recompute.
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
        raise HTTPException(
            status_code=404, detail="Film vector not found. Run embedding generation first."
        )
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
