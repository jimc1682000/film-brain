"""Heavy search-result cache + its key.

In-process cache of the EXPENSIVE search output (post-rerank, post-boost
candidate list + query understanding), keyed by the knobs that affect it.
Deliberately EXCLUDES top_k and min_display_score: those only drive the cheap
display tail (slice + per-slice band rescale), which runs live per request —
so one cached computation serves top_k 5/10/20 with correct scores (naive
slicing of a top-20 response would mis-scale the % because the band is
re-min-maxed over the shown slice). Lets startup warm the demo chips so a
click skips the ~7s CPU cross-encoder; cleared on restart, capped for memory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.pinned_lru import PinnedLRU

if TYPE_CHECKING:
    from backend.models import SearchRequest

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


def size() -> int:
    """Number of cached heavy results — public observability for tests, so
    they don't reach into the _heavy_cache module-private."""
    return len(_heavy_cache)
