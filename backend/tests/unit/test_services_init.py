"""Unit tests for the service singleton factories in backend.services.__init__."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend import services


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Clear cached singletons so each test re-runs the lazy-load + guard."""
    services._auto_tag = None
    services._feedback = None
    services._embed = None
    yield
    services._auto_tag = None
    services._feedback = None
    services._embed = None


def test_assert_llm_or_503_raises_http_503(monkeypatch):
    def _boom(backend=None):
        raise RuntimeError("LLM backend not configured")

    monkeypatch.setattr(services, "assert_ready", _boom)
    with pytest.raises(HTTPException) as exc:
        services._assert_llm_or_503()
    assert exc.value.status_code == 503
    assert "not configured" in exc.value.detail


def test_assert_llm_or_503_passes_when_ready(monkeypatch):
    monkeypatch.setattr(services, "assert_ready", lambda backend=None: None)
    # No exception expected.
    services._assert_llm_or_503()


def test_get_auto_tag_service_503_path(monkeypatch):
    monkeypatch.setattr(
        services, "assert_ready", lambda backend=None: (_ for _ in ()).throw(RuntimeError("x"))
    )
    with pytest.raises(HTTPException) as exc:
        services.get_auto_tag_service()
    assert exc.value.status_code == 503


def test_get_auto_tag_service_lazy_singleton(monkeypatch):
    monkeypatch.setattr(services, "assert_ready", lambda backend=None: None)

    class _FakeAutoTag:
        pass

    import backend.services.auto_tag as at_mod

    monkeypatch.setattr(at_mod, "AutoTagService", _FakeAutoTag)
    first = services.get_auto_tag_service()
    second = services.get_auto_tag_service()
    assert isinstance(first, _FakeAutoTag)
    assert first is second  # cached singleton


def test_get_feedback_service_lazy_singleton(monkeypatch):
    monkeypatch.setattr(services, "assert_ready", lambda backend=None: None)

    class _FakeFeedback:
        pass

    import backend.services.feedback as fb_mod

    monkeypatch.setattr(fb_mod, "FeedbackService", _FakeFeedback)
    first = services.get_feedback_service()
    second = services.get_feedback_service()
    assert isinstance(first, _FakeFeedback)
    assert first is second


def test_get_feedback_service_503_path(monkeypatch):
    monkeypatch.setattr(
        services, "assert_ready", lambda backend=None: (_ for _ in ()).throw(RuntimeError("x"))
    )
    with pytest.raises(HTTPException) as exc:
        services.get_feedback_service()
    assert exc.value.status_code == 503


def test_get_embed_service_lazy_singleton(monkeypatch):
    # No LLM guard for embeddings; just patch the heavy class.
    class _FakeEmbed:
        pass

    import backend.services.embedder as emb_mod

    monkeypatch.setattr(emb_mod, "EmbedService", _FakeEmbed)
    first = services.get_embed_service()
    second = services.get_embed_service()
    assert isinstance(first, _FakeEmbed)
    assert first is second
