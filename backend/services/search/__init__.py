"""Search service layer (ADR 0021 分層).

The business half of /api/search lives here — query planning, hybrid recall
orchestration, ranking math, the heavy result cache, and response assembly.
The FastAPI router (backend/routers/search.py) only maps HTTP requests onto
these functions; nothing in this package imports fastapi.

Modules:
  planner  — query understanding → QueryPlan (LLM expansion, tag weights)
  ranking  — display scores, weighted boost, strong-tag inject, tiers
  cache    — heavy result cache (PinnedLRU) + its key
  service  — orchestration: run_search / similar_films / pin_demo_query
"""

from backend.services.search.cache import pin_demo_query
from backend.services.search.service import (
    FilmVectorNotFoundError,
    run_search,
    similar_films,
)

__all__ = [
    "FilmVectorNotFoundError",
    "pin_demo_query",
    "run_search",
    "similar_films",
]
