"""Observability: structured JSON logging + Prometheus metrics.

Structured logging is opt-in via ``LOG_FORMAT=json`` — the default leaves
logging untouched so local dev and the test suite keep human-readable logs and
``caplog`` behaviour. Metrics are always on (incl. the slim image): a
hand-rolled ASGI middleware records request count + latency, and ``/metrics``
also reflects the cloud-LLM circuit-breaker state at scrape time. The middleware
never lets a metrics error turn a healthy response into a 500.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Awaitable, Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


# ── Structured logging ───────────────────────────────────────────────────────


class JsonLogFormatter(logging.Formatter):
    """Render a log record as one JSON line — machine-parseable for aggregation.
    Carries the exception text when the record has one."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> bool:
    """Install the JSON formatter on the root logger when ``LOG_FORMAT=json``.

    Returns True if it reconfigured logging, False (the default) if it left it
    alone — so dev/test keep human-readable logs and log capture is unaffected.
    """
    if os.getenv("LOG_FORMAT", "").lower() != "json":
        return False
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))
    return True


# ── Metrics ──────────────────────────────────────────────────────────────────

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by method, route template, and status.",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds by method and route template.",
    ["method", "path"],
)
LLM_CIRCUIT_OPEN = Gauge(
    "llm_circuit_open",
    "1 when the cloud-LLM circuit breaker is open (tagging degraded), else 0.",
)


def _route_template(request: Request) -> str:
    """Matched route path template (e.g. ``/api/films/{film_id}``) so per-id
    paths collapse to one label — bounds metric cardinality. Falls back to a
    constant when no route matched (404s)."""
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request count + latency. Any metrics failure is swallowed so it
    can never turn a healthy response into a 500."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # An unhandled route error propagates here BEFORE we'd see a response
            # (ServerErrorMiddleware turns it into the 500 the client gets). Record
            # it as a 500 so the error-rate alert catches exactly these — then
            # re-raise so the normal 500 handling still runs.
            self._record(request, 500, start)
            raise
        self._record(request, response.status_code, start)
        return response

    @staticmethod
    def _record(request: Request, status: int, start: float) -> None:
        try:
            path = _route_template(request)
            REQUESTS.labels(request.method, path, str(status)).inc()
            LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
        except Exception:  # pragma: no cover - defensive: metrics must not break a request
            logger.debug("metrics recording failed", exc_info=True)


def metrics_response() -> Response:
    """Prometheus exposition for ``/metrics``. Reflects the live LLM breaker
    state at scrape time (best-effort — never fails the scrape)."""
    try:
        from backend.llm_client import _cloud_circuit

        LLM_CIRCUIT_OPEN.set(1 if _cloud_circuit.is_open() else 0)
    except Exception:  # pragma: no cover - breaker introspection is best-effort
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
