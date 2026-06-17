"""Per-IP inbound rate limit (ADR 0025) — backend/ratelimit.py."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import ratelimit
from backend.main import app


def _req(ip="1.1.1.1"):
    return SimpleNamespace(client=SimpleNamespace(host=ip))


@pytest.fixture(autouse=True)
def _clear_hits():
    ratelimit._hits.clear()
    yield
    ratelimit._hits.clear()


def _enable(monkeypatch, limit, window=60):
    monkeypatch.setattr(
        ratelimit,
        "get_config",
        lambda: {"rate_limit": {"enabled": True, "limit": limit, "window_seconds": window}},
    )


def test_disabled_by_default_never_blocks():
    # Default config has rate_limit.enabled=False → dependency is a no-op.
    for _ in range(100):
        assert ratelimit.rate_limit_search(_req()) is None


def test_allows_up_to_limit(monkeypatch):
    _enable(monkeypatch, limit=3)
    for _ in range(3):
        assert ratelimit.rate_limit_search(_req()) is None


def test_blocks_over_limit(monkeypatch):
    _enable(monkeypatch, limit=2)
    ratelimit.rate_limit_search(_req())
    ratelimit.rate_limit_search(_req())
    with pytest.raises(HTTPException) as exc:
        ratelimit.rate_limit_search(_req())
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"]


def test_per_ip_isolated(monkeypatch):
    _enable(monkeypatch, limit=1)
    ratelimit.rate_limit_search(_req("1.1.1.1"))
    # A different IP has its own bucket.
    assert ratelimit.rate_limit_search(_req("2.2.2.2")) is None
    with pytest.raises(HTTPException):
        ratelimit.rate_limit_search(_req("1.1.1.1"))


def test_search_route_returns_429_when_over(monkeypatch):
    # End-to-end wiring: limit=0 → the dependency 429s before the search body
    # runs, proving it's attached to the route (no pipeline mocks needed).
    _enable(monkeypatch, limit=0)
    client = TestClient(app)
    r = client.post("/api/search/", json={"query": "anything"})
    assert r.status_code == 429
    assert r.headers.get("Retry-After")
