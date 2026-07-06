"""Query-understanding half of search: raw query → QueryPlan.

Builds ONE weighted tag set (explicit filters + LLM expansion, all soft), the
BM25 text, the embedded vectors, and the UI "how I read you" payload. Pure
planning — no recall/rerank happens here; the tuned ranking math stays in
ranking.py / service.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.config import settings
from backend.services.query_expand import expand_query
from backend.services.search_config import boost_weight
from backend.tag_registry import TagRegistry

if TYPE_CHECKING:
    from backend.models import SearchRequest

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


_registry_cache: TagRegistry | None = None


def _registry() -> TagRegistry:
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = TagRegistry()
    return _registry_cache


def _labels(tag_ids: list[str]) -> list[str]:
    """zh_TW labels for tag_ids (for the explainability UI)."""
    reg = _registry()
    out = []
    for t in tag_ids:
        tag = reg.get_tag(t)
        out.append((tag.get("labels", {}).get("zh_TW") if tag else None) or t)
    return out


def _excluded_tag_ids(terms: list[str]) -> set[str]:
    """Resolve user-excluded labels (gate ✕) → tag_ids. Labels aren't unique
    across dimensions → take every match. Unknown labels (an LLM keyword, not a
    taxonomy tag) resolve to nothing here; they're still stripped from the BM25
    keywords separately."""
    reg = _registry()
    out: set[str] = set()
    for term in terms:
        out.update(reg.get_tag_ids_by_label(term))
    return out


def _tag_signals(requested: dict[str, float]) -> list[dict]:
    """Structured view of the weighted tag set for the UI: tag_id + zh_TW label
    + dimension + weight. Lets the gate reference tags by id (not just label)."""
    reg = _registry()
    out = []
    for tid, w in requested.items():
        tag = reg.get_tag(tid)
        out.append(
            {
                "tag_id": tid,
                "label": (tag.get("labels", {}).get("zh_TW") if tag else None) or tid,
                "dim": tag.get("dimension") if tag else None,
                "weight": w,
            }
        )
    return out


@dataclass
class QueryPlan:
    """Everything the query-understanding half produces, consumed by the ranking
    half. Pure data — building it does NO recall/rerank. Keeps the understanding
    logic in one place; the tuned ranking math stays in the service."""

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
