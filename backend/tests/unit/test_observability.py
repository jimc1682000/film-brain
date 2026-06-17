"""Observability — structured logging + Prometheus metrics (backend/observability.py)."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from backend import observability as obs
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


# ── /metrics endpoint + middleware ───────────────────────────────────────────


def test_metrics_endpoint_exposes_prometheus(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    # Metric families are present in the exposition.
    assert "http_requests_total" in r.text
    assert "http_request_duration_seconds" in r.text
    assert "llm_circuit_open" in r.text


def test_middleware_records_request_count(client):
    client.get("/health")
    body = client.get("/metrics").text
    # The route template is the label, not the raw path.
    assert 'path="/health"' in body


def test_metrics_excluded_from_openapi(client):
    schema = client.get("/api/openapi.json").json()
    assert "/metrics" not in schema["paths"]


def test_route_template_falls_back_for_unmatched(client):
    # A 404 has no matched route → the fallback label keeps cardinality bounded.
    client.get("/no-such-route-xyz")
    body = client.get("/metrics").text
    assert 'path="unmatched"' in body


def test_unhandled_exception_recorded_as_500():
    # A route that raises must still increment a 500 counter — otherwise the
    # HighErrorRate alert would miss exactly the production 500s that matter.
    from fastapi import FastAPI
    from prometheus_client import generate_latest

    mini = FastAPI()
    mini.add_middleware(obs.MetricsMiddleware)

    @mini.get("/boom")
    def _boom():
        raise RuntimeError("kaboom")

    c = TestClient(mini, raise_server_exceptions=False)
    assert c.get("/boom").status_code == 500
    dump = generate_latest().decode()
    assert 'http_requests_total{method="GET",path="/boom",status="500"}' in dump


# ── JSON log formatter ────────────────────────────────────────────────────────


def test_json_formatter_emits_valid_json():
    rec = logging.LogRecord("svc", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    out = json.loads(obs.JsonLogFormatter().format(rec))
    assert out["level"] == "INFO"
    assert out["logger"] == "svc"
    assert out["msg"] == "hello world"
    assert "ts" in out


def test_json_formatter_carries_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = logging.LogRecord("svc", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    out = json.loads(obs.JsonLogFormatter().format(rec))
    assert "ValueError: boom" in out["exc"]


# ── configure_logging gating ──────────────────────────────────────────────────


def test_configure_logging_noop_by_default(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    assert obs.configure_logging() is False


def test_configure_logging_installs_json(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        assert obs.configure_logging() is True
        assert any(isinstance(h.formatter, obs.JsonLogFormatter) for h in root.handlers)
        assert root.level == logging.WARNING
    finally:
        root.handlers, root.level = saved_handlers, saved_level
