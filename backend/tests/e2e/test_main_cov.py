"""Coverage tests for backend.main — app setup, endpoints, lifespan warmup.

The lifespan starts a background warmup thread. We patch
backend.main.threading.Thread with a fake whose .start() runs target()
synchronously, and mock every heavy warmup dep so nothing loads a model. Using
`with TestClient(app) as c:` runs the lifespan (plain TestClient(app) does not).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import backend.main as M
from backend.config import settings
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


# ── plain (no-lifespan) endpoint coverage ────────────────────────────────────


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "tag_cache_size" in r.json()


def test_llm_info(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    monkeypatch.setattr(settings, "llm_fallback_backend", "ollama")
    monkeypatch.setattr(settings, "llm_fallback_model", "qwen2.5:1.5b")
    r = client.get("/api/llm-info")
    assert r.status_code == 200
    data = r.json()
    assert data["backend"] == "ollama"
    assert "primary_model" in data


def test_llm_info_no_fallback(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_fallback_backend", "")
    monkeypatch.setattr(settings, "llm_fallback_model", "")
    r = client.get("/api/llm-info")
    assert r.json()["fallback_backend"] is None
    assert r.json()["fallback_model"] is None


def test_llm_health(client, monkeypatch):
    monkeypatch.setattr(settings, "tagging_cloud_backend", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    r = client.get("/api/llm-health")
    assert r.status_code == 200
    data = r.json()
    assert "tagging_backend" in data
    assert "circuit" in data
    assert data["cloud_preferred"] == "openrouter"


def test_llm_health_no_cloud(client, monkeypatch):
    monkeypatch.setattr(settings, "tagging_cloud_backend", "")
    r = client.get("/api/llm-health")
    data = r.json()
    assert data["cloud_preferred"] is None
    assert data["cloud_key_present"] is False


def test_openapi_under_api_prefix(client):
    r = client.get("/api/openapi.json")
    assert r.status_code == 200


# ── lifespan + background warmup ─────────────────────────────────────────────


class _SyncThread:
    """Thread stub that runs target() synchronously on .start()."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _stub_warmups(monkeypatch):
    """Make every heavy warmup cheap/safe so the bg thread runs instantly."""

    class _Embed:
        def warmup_tag_cache(self, reg):
            return 7

    # patch get_embed_service where _bg_warmup imports it
    import backend.services as svc

    monkeypatch.setattr(svc, "get_embed_service", lambda: _Embed())

    import backend.tag_registry as treg

    monkeypatch.setattr(treg, "TagRegistry", lambda: object())

    # BM25 rebuild
    import backend.services.bm25_search as bm

    monkeypatch.setattr(bm, "rebuild_fts", lambda conn: 42)

    # cross-encoder warmup
    import backend.services.reranker as rr

    monkeypatch.setattr(rr, "warmup", lambda: True)


def test_lifespan_warmup_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(M.threading, "Thread", _SyncThread)
    _stub_warmups(monkeypatch)
    # Nonexistent chips file → _warm_demo_chips hits except-and-return.
    monkeypatch.setattr(settings, "chips_path", tmp_path / "no-such-chips.json")
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        # tag_cache_size set by the synchronous warmup
        assert r.json()["tag_cache_size"] == 7


def test_lifespan_warmup_failures_swallowed(monkeypatch, tmp_path):
    monkeypatch.setattr(M.threading, "Thread", _SyncThread)
    monkeypatch.setattr(settings, "chips_path", tmp_path / "no-such-chips.json")
    import backend.services as svc

    monkeypatch.setattr(
        svc, "get_embed_service", lambda: (_ for _ in ()).throw(RuntimeError("no model"))
    )
    import backend.services.bm25_search as bm

    monkeypatch.setattr(bm, "rebuild_fts", lambda conn: (_ for _ in ()).throw(RuntimeError("fts")))
    import backend.services.reranker as rr

    monkeypatch.setattr(rr, "warmup", lambda: (_ for _ in ()).throw(RuntimeError("ce")))
    # Each try-block logs + continues; startup must still succeed.
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_warm_demo_chips_loop(monkeypatch, tmp_path):
    """Exercise the chips loop body: one chip → no sleep(300), runs the full
    pipeline (mocked) + pin helpers."""
    monkeypatch.setattr(M.threading, "Thread", _SyncThread)
    _stub_warmups(monkeypatch)

    chips_file = tmp_path / "chips.json"
    chips_file.write_text(json.dumps(["搞笑電影"]), encoding="utf-8")
    # Point the warmup at the temp chip list (settings.chips_path).
    monkeypatch.setattr(settings, "chips_path", chips_file)

    # Sync stub: the warmup does `_asyncio.run(semantic_search(req))`; inside the
    # TestClient's running loop that nested asyncio.run raises (caught by the
    # except branch) — a sync stub avoids leaking an unawaited coroutine.
    def _fake_search(req):
        return None

    monkeypatch.setattr("backend.routers.search.semantic_search", _fake_search)
    monkeypatch.setattr("backend.routers.search.pin_demo_query", lambda req: True)
    monkeypatch.setattr("backend.services.query_expand.pin_query", lambda q: None)

    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_warm_demo_chips_pipeline_error(monkeypatch, tmp_path):
    """A chip whose pipeline raises is logged + skipped (except branch)."""
    monkeypatch.setattr(M.threading, "Thread", _SyncThread)
    _stub_warmups(monkeypatch)

    chips_file = tmp_path / "chips.json"
    chips_file.write_text(json.dumps(["壞掉的查詢"]), encoding="utf-8")
    # Point the warmup at the temp chip list (settings.chips_path).
    monkeypatch.setattr(settings, "chips_path", chips_file)

    def _boom(req):
        raise RuntimeError("pipeline down")

    monkeypatch.setattr("backend.routers.search.semantic_search", _boom)
    monkeypatch.setattr("backend.routers.search.pin_demo_query", lambda req: True)
    monkeypatch.setattr("backend.services.query_expand.pin_query", lambda q: None)

    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
