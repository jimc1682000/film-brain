"""Process-wide singleton providers (ADR 0021 DI seams) + reset_all().

Every ``get_*`` provider that hands out a lazily-built process singleton lives
here — they were previously scattered across services/__init__, reranker,
vector_store and llm_client, so tests had to poke each module's private
globals to get a clean slate. The original modules keep thin re-exports, so
existing import paths (and FastAPI ``Depends`` identity) stay valid.

``reset_all()`` is the ONE public reset switch for tests: it drops every
singleton and clears the in-process caches (heavy search cache, query-
expansion LRU, tag-registry caches). Production code never calls it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.interfaces import Embedder, LLMClient, Reranker, VectorStore

_auto_tag = None
_feedback = None
_embed: Embedder | None = None
_reranker: Reranker | None = None
_vector_store: VectorStore | None = None
_llm_client: LLMClient | None = None


def get_auto_tag_service():
    """LLM auto-tagger; raises the services 503 guard when no LLM is configured."""
    global _auto_tag
    if _auto_tag is None:
        from backend.services import _assert_llm_or_503

        _assert_llm_or_503()
        from backend.services.auto_tag import AutoTagService

        _auto_tag = AutoTagService()
    return _auto_tag


def get_feedback_service():
    """Feedback-wiki service; raises the services 503 guard when no LLM is configured."""
    global _feedback
    if _feedback is None:
        from backend.services import _assert_llm_or_503

        _assert_llm_or_503()
        from backend.services.feedback import FeedbackService

        _feedback = FeedbackService()
    return _feedback


def get_embed_service() -> Embedder:
    """Embedding service has no LLM backend; loads BAAI/bge-m3 weights lazily.

    First call can take several seconds — model load — so callers should
    not rely on per-request creation. Return type is the `Embedder` Protocol
    (ADR 0021) — the seam where a fake embedder can be injected in tests.
    """
    global _embed
    if _embed is None:
        from backend.services.embedder import EmbedService

        _embed = EmbedService()
    return _embed


def get_reranker() -> Reranker:
    """Return the process-wide Reranker (ADR 0021 injection seam).

    Consumers depend on the `Reranker` Protocol and resolve the concrete impl
    through this provider, so a fake can be injected (FastAPI dependency
    override / direct param) without monkeypatching the module function.
    """
    global _reranker
    if _reranker is None:
        from backend.services.reranker import CrossEncoderReranker

        _reranker = CrossEncoderReranker()
    return _reranker


def get_vector_store() -> VectorStore:
    """Return the process-wide VectorStore (ADR 0021 injection seam).

    Consumers depend on the `VectorStore` Protocol and resolve the concrete impl
    through this provider, so a fake can be injected (param / FastAPI override)
    without monkeypatching the module function.
    """
    global _vector_store
    if _vector_store is None:
        from backend.vector_store import QdrantVectorStore

        _vector_store = QdrantVectorStore()
    return _vector_store


def get_llm_client() -> LLMClient:
    """Return the process-wide LLMClient (ADR 0021 injection seam)."""
    global _llm_client
    if _llm_client is None:
        from backend.llm_client import DefaultLLMClient

        _llm_client = DefaultLLMClient()
    return _llm_client


def reset_all() -> None:
    """Drop every singleton + clear the in-process caches. Test-only hook.

    Singletons are lazily rebuilt on next use, so calling this between tests
    is always safe — it just guarantees no state (cached search results,
    cached query expansions, registry caches, fake instances) leaks across
    test boundaries.
    """
    global _auto_tag, _feedback, _embed, _reranker, _vector_store, _llm_client
    _auto_tag = _feedback = _embed = _reranker = _vector_store = _llm_client = None

    # In-process caches living on module globals (not provider singletons).
    from backend.services import query_expand
    from backend.services.search import cache as search_cache
    from backend.services.search import planner

    search_cache._heavy_cache.clear()
    query_expand._cache.clear()
    query_expand._registry = None
    planner._registry_cache = None
    planner._award_tag_ids = None
