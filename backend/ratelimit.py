"""Inbound per-IP rate limit (ADR 0025).

A sliding-window counter keyed by client IP, exposed as a FastAPI dependency for
the search / similar routes. Disabled by default (internal-demo mode unchanged);
an external deployment turns it on in search-config. In-memory — fine for the
single-instance reality (counts don't span replicas; see ADR blind spots).

Behind Traefik, run uvicorn with --proxy-headers so request.client.host is the
real client IP, not the proxy (else every caller shares one bucket).
"""

from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

from backend.services.search_config import get_config

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}  # client IP -> request timestamps within the window


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit_search(request: Request) -> None:
    """Raise 429 if this client IP exceeded the configured search rate. A no-op
    when rate_limit.enabled is false (the default), so local/CI/internal-demo are
    unaffected."""
    cfg = get_config()["rate_limit"]
    if not cfg.get("enabled"):
        return
    limit = int(cfg["limit"])
    window = float(cfg["window_seconds"])
    ip = _client_ip(request)
    now = time.monotonic()
    with _lock:
        recent = [t for t in _hits.get(ip, []) if now - t < window]
        if len(recent) >= limit:
            oldest = recent[0] if recent else now
            retry_after = max(1, round(window - (now - oldest)))
            _hits[ip] = recent
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please retry later.",
                headers={"Retry-After": str(retry_after)},
            )
        recent.append(now)
        _hits[ip] = recent
