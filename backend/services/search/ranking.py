"""Ranking half of search: display scores, weighted boost, strong-tag inject.

Operates on the candidate dicts produced by hybrid recall. All signals are
soft weights on the same score channel (never hard filters), so an
all-excluded pool yields an honest empty list, not a crash.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from backend.db import get_film, get_film_tags

if TYPE_CHECKING:
    from backend.interfaces import Reranker
    from backend.models import SearchRequest


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
