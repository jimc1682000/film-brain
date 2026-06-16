"""Cross-encoder re-ranker for semantic search.

Vector search (bge-m3 bi-encoder) surfaces candidates by token overlap but can't
distinguish tone — `family` tag appears on horror AND heartwarming films.
A cross-encoder scores (query, doc) pairs directly, giving real-valued scores
that actually spread, unlike LLM JSON scoring which buckets at 0.9/1.0.

Model: BAAI/bge-reranker-v2-m3 (multilingual, zh/en native, ~568MB).
Inspired by qmd (https://github.com/tobi/qmd) which uses Qwen3-Reranker locally.

Fails open: on model load / inference error, returns input order unchanged.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from backend.db import get_db

if TYPE_CHECKING:
    from backend.interfaces import Reranker

_model = None
_model_lock = threading.Lock()

# Serialize CE inference. On the 2-vCPU demo box, two concurrent predict()
# calls peg both cores and the whole host goes unresponsive (observed: ~10 min
# hang). A 1-permit semaphore caps CE to one running inference, leaving a core
# for everything else. Callers that can't get the permit within
# _CE_QUEUE_TIMEOUT skip CE and fall back to RRF order (router handles None) —
# so concurrent demo traffic degrades gracefully instead of piling up.
_ce_gate = threading.Semaphore(1)
_CE_QUEUE_TIMEOUT = 8.0  # seconds a request waits for the CE slot before falling back
# NetEase BCE reranker is Chinese-domain-trained (MS MARCO + Chinese IR),
# better aligned for our zh film descriptions than bge-reranker-v2-m3 which
# is English-leaning. Same CrossEncoder API surface, drop-in swap.
_model_name = "maidalun1020/bce-reranker-base_v1"


def _fetch_meta(film_ids: list[str]) -> dict[str, dict]:
    """Fetch a richer meta blob per film for the cross-encoder document.

    Old impl only pulled description + tmdb_overview, so the CE input was a
    single paragraph. We now also surface release_year, region (from
    country_codes), and director/cast — structured signal that helps the CE
    distinguish "Korean crime thriller" from a horror or a Japanese film that
    happens to share a plot keyword.
    """
    if not film_ids:
        return {}
    placeholders = ",".join("?" * len(film_ids))
    with get_db() as conn:
        # release_year / country_codes / catchplay_director / catchplay_cast are
        # added by the enrichment pipeline, not the base schema — so on a fresh
        # (e.g. just-seeded / public-sample) DB they may not exist yet. Query
        # only the columns actually present; missing ones degrade to None so the
        # CE still gets description-grounded docs instead of crashing.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(films)").fetchall()}
        optional = [
            c
            for c in (
                "release_year",
                "country_codes",
                "catchplay_director",
                "catchplay_cast",
                "tmdb_director",
                "tmdb_cast",
            )
            if c in cols
        ]
        select_cols = ["film_id", "description", "tmdb_overview", *optional]
        rows = conn.execute(
            f"SELECT {', '.join(select_cols)} FROM films WHERE film_id IN ({placeholders})",
            film_ids,
        ).fetchall()

    selected = set(select_cols)

    def _g(r, col):
        # sqlite3.Row `in` checks values, so test against the columns we selected
        return r[col] if col in selected else None

    out: dict[str, dict] = {}
    for r in rows:
        out[r["film_id"]] = {
            "desc": ((r["description"] or r["tmdb_overview"] or "").strip())[:400],
            "year": _g(r, "release_year"),
            "country": _g(r, "country_codes"),
            "director": _g(r, "catchplay_director") or _g(r, "tmdb_director"),
            "cast": _g(r, "catchplay_cast") or _g(r, "tmdb_cast"),
        }
    return out


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import CrossEncoder

            _model = CrossEncoder(_model_name, max_length=512)
    return _model


def warmup() -> bool:
    """Load the CE model ahead of the first request (called at startup) so the
    demo's first search doesn't pay the ~400MB cold load. Safe to call twice."""
    try:
        _get_model()
        return True
    except Exception:
        return False


def _doc_text(cand: dict, meta: dict | None = None) -> str:
    """Compose searchable doc text — title + year + region + tags + cast +
    short description.

    Tags alone can't distinguish nuance (crime vs horror both have `thriller`);
    a description sentence gives the CE model enough grounding to decide.
    Adding year + region + director/cast helps with queries that name regions
    or directors implicitly (e.g. "韓國犯罪驚悚" pushes Korean films over
    visually similar Japanese ones).
    """
    meta = meta or {}
    parts = []
    title_zh = cand.get("title_zh", "")
    title_en = cand.get("title_en") or ""
    head = title_zh
    if title_en:
        head = f"{head} / {title_en}" if head else title_en
    if meta.get("year"):
        head = f"{head} ({meta['year']})" if head else f"({meta['year']})"
    if meta.get("country"):
        head = f"{head} {meta['country']}"
    if head:
        parts.append(head)
    tags = cand.get("tags") or []
    if tags:
        parts.append("標籤: " + ", ".join(tags))
    if meta.get("director"):
        parts.append("導演: " + str(meta["director"])[:60])
    if meta.get("cast"):
        parts.append("卡司: " + str(meta["cast"])[:80])
    if meta.get("desc"):
        parts.append("劇情: " + meta["desc"])
    return " | ".join(parts)


def rerank_with_cross_encoder(
    query: str,
    candidates: list[dict],
) -> list[dict] | None:
    """Score (query, candidate) pairs with a cross-encoder, return reordered list.

    Uses raw CE logits for ranking, then min-max normalizes to 0-1 for display.
    Pure CE (no vector blend) — the whole point of reranking is to override
    the bi-encoder's verdict when it gets tone / intent wrong.
    """
    if not candidates:
        return candidates

    # Serialize: only one CE inference at a time. If the slot is busy and we
    # can't get it in time, skip CE → caller falls back to RRF order.
    if not _ce_gate.acquire(timeout=_CE_QUEUE_TIMEOUT):
        return None
    try:
        try:
            model = _get_model()
        except Exception:
            return None

        meta = _fetch_meta([c["film_id"] for c in candidates])
        pairs = [(query, _doc_text(c, meta.get(c["film_id"]))) for c in candidates]
        try:
            raw = [float(s) for s in model.predict(pairs, show_progress_bar=False).tolist()]
        except Exception:
            return None
    finally:
        _ce_gate.release()

    # Position-aware blend with the pre-CE retrieval score (qmd-style — see
    # ADR 0001). The pre-CE order already encodes RRF + injected strong-dim
    # films; CE is a precision tweak, not an override. Trust the top of the
    # pre-CE list more (the bi-encoder + boost rarely puts irrelevant films
    # at rank 1-3) and let CE dominate further down the list where the
    # retrieval signal is noisier.
    lo_c, hi_c = min(raw), max(raw)
    span_c = hi_c - lo_c if hi_c > lo_c else 1.0
    rrf_scores = [c.get("rrf_score", 0.0) for c in candidates]
    lo_r, hi_r = min(rrf_scores), max(rrf_scores)
    span_r = hi_r - lo_r if hi_r > lo_r else 1.0

    enriched = []
    for c, r in zip(candidates, raw, strict=False):
        merged = dict(c)
        pre_rank = c.get("_pre_ce_rank", 999)
        if pre_rank <= 2:  # original rank 1-3 → trust retrieval more
            w_ce = 0.25
        elif pre_rank <= 9:  # rank 4-10 → split
            w_ce = 0.4
        else:  # rank 11+ → trust CE more (long tail)
            w_ce = 0.6
        rrf_norm = (c.get("rrf_score", 0.0) - lo_r) / span_r
        ce_norm = (r - lo_c) / span_c
        blended = (1.0 - w_ce) * rrf_norm + w_ce * ce_norm
        merged["llm_score"] = blended  # normalized 0-1 for display
        merged["ce_logit"] = r
        merged["ce_blend_w"] = w_ce
        merged["llm_reason"] = ""
        enriched.append(merged)

    enriched.sort(key=lambda x: -x["llm_score"])
    return enriched


class CrossEncoderReranker:
    """Adapter wrapping `rerank_with_cross_encoder` behind the Reranker Protocol.

    The function predates the Protocol and uses a different name (`rerank` vs
    `rerank_with_cross_encoder`), so the module can't structurally satisfy the
    contract — the wrapper is mandatory here (ADR 0021). It delegates to the
    existing function, preserving the fail-open semantics and letting
    source-level patches of the function keep working.
    """

    def rerank(self, query: str, candidates: list[dict]) -> list[dict] | None:
        return rerank_with_cross_encoder(query, candidates)


_reranker: CrossEncoderReranker | None = None


def get_reranker() -> Reranker:
    """Return the process-wide Reranker (ADR 0021 injection seam).

    Consumers depend on the `Reranker` Protocol and resolve the concrete impl
    through this provider, so a fake can be injected (FastAPI dependency
    override / direct param) without monkeypatching the module function.
    """
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoderReranker()
    return _reranker
