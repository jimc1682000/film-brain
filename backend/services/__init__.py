"""Service singleton factories — re-exported from backend.providers.

Routers used to maintain their own `_service = None` global plus an LLM
readiness guard. The singleton state now lives centrally in backend.providers
(one `reset_all()` switch for tests); these aliases keep the historical import
path (`from backend.services import get_embed_service` …) working, and the
single `HTTPException` helper below still handles the 503 path for LLM-backed
services.
"""

from __future__ import annotations

from fastapi import HTTPException

from backend.llm_client import assert_ready
from backend.providers import (
    get_auto_tag_service as get_auto_tag_service,
)
from backend.providers import (
    get_embed_service as get_embed_service,
)
from backend.providers import (
    get_feedback_service as get_feedback_service,
)


def _assert_llm_or_503() -> None:
    """Raise FastAPI 503 with a clear message when the LLM backend is unconfigured."""
    try:
        assert_ready()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
