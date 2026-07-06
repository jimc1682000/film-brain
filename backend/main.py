import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.db import init_db
from backend.observability import MetricsMiddleware, configure_logging, metrics_response
from backend.routers import auto_tag, awards, feedback, films, reviews, search, tags

# Opt-in structured logging (LOG_FORMAT=json); a no-op otherwise.
configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: only the cheap, must-have step (DB schema) runs synchronously so
    # the service becomes healthy IMMEDIATELY. Every heavy warmup — tag-vector
    # cache, BM25 FTS rebuild, cross-encoder load (may pull from HF), demo chips
    # — runs in a background thread. A heavy/slow/hanging warmup therefore can no
    # longer block startup (was a 502 source); the first request that needs an
    # unwarmed piece just loads it lazily.
    init_db()
    app.state.tag_cache_size = 0

    def _bg_warmup():
        try:
            from backend.services import get_embed_service
            from backend.tag_registry import TagRegistry

            count = get_embed_service().warmup_tag_cache(TagRegistry())
            app.state.tag_cache_size = count
            print(f"[startup] Tag vector cache warmed: {count} vectors", flush=True)
        except Exception as e:
            logger.warning("Tag vector cache warmup failed: %s", e)

        try:
            from backend.db import get_db
            from backend.services.bm25_search import rebuild_fts

            with get_db() as conn:
                n = rebuild_fts(conn)
            print(f"[startup] BM25 FTS index rebuilt: {n} films", flush=True)
        except Exception as e:
            logger.warning("BM25 FTS rebuild failed: %s", e)

        try:
            from backend.services.reranker import warmup as warmup_ce

            print(f"[startup] Cross-encoder warmed: {warmup_ce()}", flush=True)
        except Exception as e:
            logger.warning("Cross-encoder warmup failed: %s", e)

        # Warm the demo chips through the FULL search pipeline (service call,
        # not the HTTP handler) so clicking a chip during the demo returns from
        # the result cache (~instant) — the dominant cost is the CPU
        # cross-encoder rerank (~7s, Semaphore(1)), not query expansion.
        # Non-blocking: readiness isn't delayed; chips fill in over the next
        # few minutes. Loop + throttling live in services.demo_warmup.
        from backend.services.demo_warmup import warm_demo_chips

        warm_demo_chips()

    threading.Thread(target=_bg_warmup, daemon=True).start()
    yield
    # Shutdown: nothing to clean up for now


app = FastAPI(
    title="AI Film Library Brain",
    description="CATCHPLAY+ AI-powered film tagging and semantic search",
    version="0.1.0",
    lifespan=lifespan,
    # Traefik only forwards /api/* to the backend on the demo VPS, so the
    # default root-level /docs is unreachable there. Mount Swagger + the
    # OpenAPI schema under /api so the same URL works locally and in prod.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Request count + latency metrics on every route (degrades silently on error).
app.add_middleware(MetricsMiddleware)

app.include_router(films.router, prefix="/api/films", tags=["Films"])
app.include_router(tags.router, prefix="/api/tags", tags=["Tags"])
app.include_router(auto_tag.router, prefix="/api/auto-tag", tags=["Auto-Tag"])
app.include_router(search.router, prefix="/api/search", tags=["Search"])
app.include_router(reviews.router, prefix="/api", tags=["Reviews"])
app.include_router(awards.router, tags=["Awards"])
app.include_router(feedback.router, prefix="/api/feedback", tags=["Feedback"])


@app.get("/api/llm-info")
def llm_info():
    """Active LLM config — so the UI shows the model that actually runs
    instead of a hard-coded name (backend/fallback can change via env)."""
    from backend.config import settings
    from backend.llm_client import select_model

    return {
        "backend": settings.llm_backend,
        "primary_model": select_model(),
        "fallback_backend": settings.llm_fallback_backend or None,
        "fallback_model": settings.llm_fallback_model or None,
    }


@app.get("/api/llm-health")
def llm_health():
    """Which LLM path auto-tag will take right now, and why.

    The 8GB CPU box can't run the auto-tag prompt usefully on the local model,
    so tagging prefers a cloud backend guarded by a circuit breaker. This
    surfaces the live decision (cloud vs local) + the breaker state so the UI —
    and we — can see whether cloud is in play without reading logs."""
    from backend.config import settings
    from backend.llm_client import (
        _cloud_circuit,
        _has_api_key,
        cloud_tagging_available,
        select_tagging_backend,
    )

    cloud = settings.tagging_cloud_backend
    return {
        "tagging_backend": select_tagging_backend(),
        "cloud_preferred": cloud or None,
        "cloud_key_present": _has_api_key(cloud) if cloud else False,
        "cloud_available_now": cloud_tagging_available(),
        "circuit": _cloud_circuit.status(),
        "local_fallback_model": settings.llm_fallback_model or settings.primary_model,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "tag_cache_size": getattr(app.state, "tag_cache_size", 0),
    }


@app.get("/metrics", include_in_schema=False)
def metrics():
    """Prometheus exposition (excluded from the OpenAPI schema)."""
    return metrics_response()
