"""Hot-reloaded search tuning config (single source of truth).

All search knobs — RRF weights, recall/pool sizes, score floor, per-dimension
filter/boost policy — live in data/search-config.json. This module loads it,
caching by file mtime so editing the file takes effect on the next search with
no restart (data/ is a mounted volume on the VPS). Missing keys fall back to
_DEFAULTS, so a malformed/absent file never breaks search.

See ADR 0002. Values are hand-set priors; tune via the eval harness (P5).
"""

from __future__ import annotations

import json
import threading

from backend.config import settings

_PATH = settings.db_path.parent / "search-config.json"

_DEFAULTS: dict = {
    "recall": 40,
    "pool": 30,
    "rrf_k": 60,
    "top_bonus": [0.02, 0.01, 0.005],
    "weights": {"vector": 1.0, "hyde": 0.7, "bm25": 0.8},
    "min_display_score": 0.1,
    # Out-of-domain gate: if the best candidate's cosine to the USER query
    # vector (primary_cos, not HyDE) falls below this, the library has no real
    # match — results are semantic guesses. Calibrated 2026-06: good demo
    # queries top-1 cluster at 0.49-0.63, "Michael Jackson" at 0.37.
    "low_confidence_cosine": 0.45,
    # Display bands: ranking-internal scores are relative (min-max / CE blend),
    # so the raw top-1 always reads 100% — meaningless to users. Map [0,1] into
    # a band so the shown % is never a fake perfect score; the low band also
    # caps tag-boosted hits so an out-of-domain query can't show green.
    "display_band": {"confident": [0.55, 0.95], "low": [0.3, 0.65]},
    # Confidence tiers keyed off the best query-vector cosine (primary_cos): pick
    # high/mid/low by min_cos, each mapping to a display band ceiling. Required by
    # _confidence_tier — kept in _DEFAULTS so an absent/partial config file never
    # KeyErrors (honours this module's "absent file never breaks search" contract).
    "confidence_tiers": {
        "high": {"min_cos": 0.52, "band": [0.72, 0.95]},
        "mid": {"min_cos": 0.45, "band": [0.45, 0.68]},
        "low": {"min_cos": 0.0, "band": [0.20, 0.42]},
    },
    # Unified weighted-boost model (no hard filters): a candidate gains
    # tag_boost_scale * Σ(dim weight) for each requested tag it carries.
    "tag_boost_scale": 0.15,
    # Penalty subtracted from display_score per excluded tag a film carries
    # (user said 不要X via the gate ✕). Large by default so any film tagged with
    # an excluded tag drops below min_display_score and disappears — matches the
    # literal "不要X" intent. Lower it for soft demotion instead of removal.
    # Never a hard filter: same score channel as boost, so an all-excluded pool
    # yields an honest (possibly empty) list, not a crash.
    "exclude_penalty": 10.0,
    # Dims at/above this weight are "strong" — their films get injected into the
    # pool when requested (so e.g. 得獎/地區 always have results), not left to
    # whether recall happened to surface them.
    "inject_weight_threshold": 1.5,
    # Policy for any dimension NOT explicitly listed — low-weight soft boost.
    "dimension_default": {"mode": "boost", "weight": 0.3},
    "dimensions": {
        "region": {"mode": "boost", "weight": 2.0},
        "ip": {"mode": "boost", "weight": 1.8},
        "award": {"mode": "boost", "weight": 1.5},
        "audience": {"mode": "boost", "weight": 1.5},
    },
    # Prompt-injection input gate (OWASP LLM01 — backend/services/prompt_guard.py):
    # score thresholds + per-signal weights. SUSPICIOUS is deliberately wide
    # ("prefer over-catch") — it only logs/escalates, never hard-denies; BLOCK
    # stays conservative since it degrades search. Tunable here without a deploy.
    "prompt_guard": {
        "block": 50,
        "suspicious": 10,
        "weights": {"high": 35, "medium": 20, "base64": 30, "unicode": 25, "char_ratio": 15},
        "char_ratio_min_len": 30,
        "char_ratio_threshold": 0.25,
    },
    # Inbound per-IP rate limit on search/similar (ADR 0025). Disabled by default
    # (internal-demo mode unchanged); an external deployment sets enabled=true.
    # Fixed window: `limit` requests per `window_seconds` per client IP → 429.
    "rate_limit": {"enabled": False, "limit": 30, "window_seconds": 60},
}

_lock = threading.Lock()
_cache: dict = {"mtime": None, "data": _DEFAULTS}


def get_config() -> dict:
    """Return the current config, reloading the file if it changed on disk."""
    try:
        mtime = _PATH.stat().st_mtime
    except OSError:
        return _DEFAULTS
    if _cache["mtime"] == mtime:
        return _cache["data"]
    with _lock:
        try:
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        cfg = {**_DEFAULTS, **{k: v for k, v in raw.items() if not k.startswith("_")}}
        cfg["weights"] = {**_DEFAULTS["weights"], **raw.get("weights", {})}
        cfg["dimensions"] = {**_DEFAULTS["dimensions"], **raw.get("dimensions", {})}
        cfg["dimension_default"] = {
            **_DEFAULTS["dimension_default"],
            **raw.get("dimension_default", {}),
        }
        # Merge prompt_guard nested (incl. its inner `weights`) so a partial
        # override — e.g. just {"block": 30} — keeps the other keys instead of
        # dropping them and making prompt_guard.inspect() KeyError on every query.
        pg_raw = raw.get("prompt_guard", {})
        cfg["prompt_guard"] = {**_DEFAULTS["prompt_guard"], **pg_raw}
        cfg["prompt_guard"]["weights"] = {
            **_DEFAULTS["prompt_guard"]["weights"],
            **pg_raw.get("weights", {}),
        }
        cfg["rate_limit"] = {**_DEFAULTS["rate_limit"], **raw.get("rate_limit", {})}
        _cache.update(mtime=mtime, data=cfg)
    return _cache["data"]


def _policy_for(dimension: str) -> dict:
    """Resolve a dimension's policy — explicit config entry, else the default
    (so taxonomy dims added later are handled without a code change)."""
    cfg = get_config()
    # Drop note-only keys (e.g. "_") from the entry before use.
    entry = {k: v for k, v in cfg["dimensions"].get(dimension, {}).items() if not k.startswith("_")}
    return entry or cfg["dimension_default"]


def dim_mode(dimension: str) -> str:
    return _policy_for(dimension).get("mode", "boost")


def boost_weight(dimension: str) -> float:
    """Soft-boost weight for a dimension; 0 unless its mode is boost."""
    p = _policy_for(dimension)
    return float(p.get("weight", 0.0)) if p.get("mode") == "boost" else 0.0
