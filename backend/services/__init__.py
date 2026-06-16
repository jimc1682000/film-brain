"""Service singleton factories — one lazy-load per service.

Routers used to maintain their own `_service = None` global plus an LLM
readiness guard. Now they import `get_auto_tag_service()` /
`get_feedback_service()` / `get_embed_service()` and a single `HTTPException`
helper handles the 503 path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from backend.llm_client import assert_ready

if TYPE_CHECKING:
    from backend.interfaces import Embedder

_auto_tag = None
_feedback = None
_embed = None


def _assert_llm_or_503() -> None:
    """Raise FastAPI 503 with a clear message when the LLM backend is unconfigured."""
    try:
        assert_ready()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def get_auto_tag_service():
    global _auto_tag
    if _auto_tag is None:
        _assert_llm_or_503()
        from backend.services.auto_tag import AutoTagService

        _auto_tag = AutoTagService()
    return _auto_tag


def get_feedback_service():
    global _feedback
    if _feedback is None:
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
