"""Demo-chip warmup: push the demo queries through the FULL search pipeline.

Runs in the lifespan's background thread (lifespan only schedules; the loop
lives here). Each chip runs the same pipeline a frontend chip click triggers —
directly via the search service with a real Reranker, not through the HTTP
handler (calling the route function passed the unresolved ``Depends`` default
as the reranker) — so the result-cache key matches and a clicked chip returns
from cache (~instant), skipping the ~7s CPU cross-encoder rerank.

Reads the SAME chips file the frontend renders (settings.chips_path; compose
mounts it read-only at /app/chips.json and sets CHIPS_PATH) — single source,
no drift. Throttled to 1 req / 5 min: OpenRouter free tier is stable at low
QPS but bursting N chips at startup exhausts quota quickly.
"""

from __future__ import annotations

import json
import logging
import time

from backend.config import settings
from backend.models import SearchRequest
from backend.services.query_expand import pin_query
from backend.services.reranker import get_reranker
from backend.services.search import pin_demo_query, run_search

logger = logging.getLogger(__name__)

_CHIP_INTERVAL_S = 300  # 1 req / 5 min — keep within OpenRouter free-tier QPS


def warm_demo_chips() -> int:
    """Warm + pin every demo chip; returns how many warmed successfully."""
    try:
        chips = json.loads(settings.chips_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.info("Demo-chip warm skipped (no chips file): %s", e)
        return 0
    warmed = 0
    for i, q in enumerate(chips):
        if i > 0:
            time.sleep(_CHIP_INTERVAL_S)
        try:
            # SAME params the frontend chip click sends (top_k=10) so the
            # result-cache key matches the click.
            req = SearchRequest(query=q, top_k=10)
            run_search(req, get_reranker())
            # Pin both cache layers so audience reloop churn can never evict
            # the demo entries — the stage demo always hits a warm cache.
            pin_demo_query(req)
            pin_query(q)
            warmed += 1
        except Exception as e:
            logger.info("Demo-chip warm failed for %r: %s", q, e)
    print(f"[startup] Demo-chip full-search warmed: {warmed}/{len(chips)} queries", flush=True)
    return warmed
