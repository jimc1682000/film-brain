"""LLM query understanding — structured taxonomy filters + generative expansion.

One LLM call (the configured primary — currently glm-4.5-air via OpenRouter,
Gemini as fallback) turns a raw query into three things (see ADR 0002):
  - filters   : 14-dim taxonomy tag_ids (hard constraints, validated against
                the registry so hallucinated tags are dropped)
  - hyde_text : a short hypothetical plot snippet, embedded for extra vector
                recall (qmd's HyDE idea)
  - keywords  : BM25 keywords for extra lexical recall

Reuses the existing llm_client (primary + fallback already configured) — no new
model, no fine-tune. Results are cached per normalised query; on any LLM failure
the caller degrades to plain vector + BM25 search (no tag boosts), so search
never blocks. This module is the SOLE query-understanding path — the old regex
keyword parser was folded in here (its region/genre/setting work is what the LLM
already does, and award-presence is now an LLM-emitted flag).
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING

from backend.llm_client import (
    LLMRateLimitError,
    get_llm_client,
    select_model,
    strip_json_fence,
)
from backend.services import prompt_guard
from backend.services.pinned_lru import PinnedLRU
from backend.services.search_config import boost_weight, dim_mode
from backend.tag_registry import TagRegistry

if TYPE_CHECKING:
    from backend.interfaces import LLMClient

logger = logging.getLogger(__name__)


def _loggable(s: object) -> str:
    """Strip CR/LF from a user-supplied value before logging it, so it can't
    forge log lines (log injection)."""
    return str(s).replace("\r", " ").replace("\n", " ")


# Which dims are hard filters vs soft boosts is data-driven (search-config.json,
# hot-reloaded): filter dims are exclusionary (region/award/…), boost dims are
# preferential and would over-constrain if ANDed — a long query like
# "被仙人跳而分手該怎麼療傷" makes the LLM emit tags across many dims, and ANDing
# them all empties the candidate set. Boost-dim tags become a soft score boost
# (boost_tags) + a BM25 keyword (recall), not a hard constraint.

_registry: TagRegistry | None = None
# Bounded LRU (was an unbounded dict): the gate/reloop loop turns every
# correction into a distinct effective query, each a cold one-off — without a
# cap the cache grows forever. Demo-chip expansions are pinned at warmup so they
# survive any amount of audience reloop churn. ~256 non-pinned × ~2KB ≈ 0.5MB.
_cache = PinnedLRU(256)
_lock = threading.Lock()


def pin_query(query: str) -> bool:
    """Pin a query's cached expansion so it survives LRU eviction. Called at
    warmup for demo chips. Key is the same normalised form expand_query uses."""
    with _lock:
        return _cache.pin((query or "").strip())


_EMPTY: dict = {
    "filters": {},
    "hyde_text": "",
    "keywords": [],
    "boost_tags": [],
    "stepback_text": "",
    "award_presence": False,
}


def _degraded() -> dict:
    """Empty expansion flagged as a FAILURE (LLM rate-limit / error / bad JSON),
    so callers can avoid caching a degraded result (vs a genuinely empty one)."""
    d = dict(_EMPTY)
    d["_degraded"] = True
    return d


_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string"}},
        "hyde": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "stepback": {"type": "string"},
        "award_presence": {"type": "boolean"},
    },
    "required": ["tags", "hyde", "keywords"],
}


def _reg() -> TagRegistry:
    global _registry
    if _registry is None:
        _registry = TagRegistry()
    return _registry


def _system_prompt(reg: TagRegistry) -> str:
    return (
        "你是電影語意搜尋的查詢理解器。**所有輸出一律使用繁體中文(台灣用語),"
        "嚴禁出現任何簡體字。**使用者給一句中文搜尋,你要產出 JSON:\n"
        "1. tags:從下方 taxonomy 挑出與查詢相關的 tag_id(只能用清單裡存在的 id,"
        "不確定就少給,不要硬湊;最多 8 個)。**特別注意 emotion 維度**——"
        "若查詢有情緒/氛圍訊號(療癒/虐戀/催淚/溫馨/驚嚇/浪漫等)務必選對應 tag。\n"
        "2. hyde:用 1-2 句中文寫一段「最符合這個查詢的電影劇情大綱」假想描述"
        "(幫助向量檢索,不要提到 tag)。\n"
        "3. keywords:3-6 個適合全文檢索的中文關鍵詞(片名、題材、地區、情境)。"
        "**只能從查詢本身的語意延伸,嚴禁杜撰查詢沒提到的具體人事物**"
        "(例:查詢『諜報動作片』不可生出『飛鳥』這種無關詞)。\n"
        "4. stepback:把查詢「抽象化」成一句 8-15 字的上層概念(去掉具體情境細節,"
        "只留主題與情緒)。例:"
        "「被仙人跳而分手該怎麼療傷」→「失戀療癒劇情片」;"
        "「韓國犯罪驚悚片」→「黑暗緊張的犯罪故事」。\n"
        "5. award_presence:若查詢泛指『得獎/入圍/獎項/winner/nominated』電影、"
        "但沒指明哪一個獎,設 true;否則 false。\n\n"
        f"{reg.to_prompt_context()}"
    )


def _call_expansion_llm(llm, q: str, reg, timeout: float) -> str | None:
    """One LLM call → raw text, or None on any failure (logged).

    Split the failure modes so the log distinguishes (a) cloud free-tier quota
    (LLMRateLimitError), (b) local Qwen3 thinking-budget / retries exhausted
    (RuntimeError from _call_openrouter), (c) transport / unknown. All degrade
    to None → caller's BM25/keyword path is the floor; the log lines explain why.
    """
    try:
        return llm.call_llm(
            _system_prompt(reg), q, model=select_model(), schema=_SCHEMA, timeout=timeout, meta={}
        )
    except LLMRateLimitError as e:
        logger.warning("query_expand rate-limited for %r: %s", _loggable(q), e)
    except RuntimeError as e:
        logger.warning("query_expand exhausted for %r: %s", _loggable(q), e)
    except Exception as e:  # transport / unknown — degrade, don't block
        logger.warning("query_expand failed for %r: %s", _loggable(q), e)
    return None


def _parse_expansion(data: dict, reg) -> dict:
    """Validate the LLM's tags against the registry, split into hard filters vs
    soft boost_tags (+ their zh labels as BM25 keywords) by per-dim policy, and
    assemble the expansion result dict."""
    valid, _invalid = reg.validate_tag_ids([t for t in data.get("tags", []) if isinstance(t, str)])
    filters: dict[str, list[str]] = {}
    boost_tags: list[list] = []  # [[tag_id, weight], ...] — soft score boost
    soft_terms: list[str] = []
    for tid in valid:
        tag = reg.get_tag(tid)
        if not tag:
            continue
        dim = tag["dimension"]
        if dim_mode(dim) == "filter":
            filters.setdefault(dim, []).append(tid)
        else:
            # Preferential dim → soft score boost + BM25 keyword (recall),
            # never a hard constraint. Weight is per-dim, from search-config.
            w = boost_weight(dim)
            if w > 0:
                boost_tags.append([tid, w])
            label = (tag.get("labels", {}) or {}).get("zh_TW")
            if label:
                soft_terms.append(label)

    keywords = [k.strip() for k in data.get("keywords", []) if isinstance(k, str) and k.strip()]
    # De-dupe soft tag labels into the keyword list.
    for term in soft_terms:
        if term not in keywords:
            keywords.append(term)
    return {
        "filters": filters,
        "hyde_text": (data.get("hyde") or "").strip(),
        "keywords": keywords[:12],
        "boost_tags": boost_tags,
        "stepback_text": (data.get("stepback") or "").strip(),
        "award_presence": bool(data.get("award_presence")),
    }


def expand_query(query: str, *, timeout: float = 20.0, llm_client: LLMClient | None = None) -> dict:
    """Return {filters: {dim: [tag_id]}, hyde_text, keywords}.

    Cached per query. On LLM error / timeout returns the empty expansion so the
    caller falls back to keyword parsing only. `llm_client` is the ADR 0021
    injection seam (defaults to the process-wide client).
    """
    q = (query or "").strip()
    if not q:
        return dict(_EMPTY)
    if q in _cache:
        return _cache[q]

    # Prompt-injection input gate (OWASP LLM01). BLOCK → skip the LLM and degrade
    # to BM25 (never hard-fail search); SUSPICIOUS → log and proceed (the output
    # is still validated against the registry downstream).
    guard = prompt_guard.inspect_deep(q)
    if guard.level is prompt_guard.RiskLevel.BLOCK:
        logger.warning(
            "prompt-injection blocked (score=%d %s) for %r",
            guard.score,
            guard.matched,
            _loggable(q),
        )
        return _degraded()
    if guard.level is prompt_guard.RiskLevel.SUSPICIOUS:
        logger.info(
            "prompt-injection suspicious (score=%d %s) for %r",
            guard.score,
            guard.matched,
            _loggable(q),
        )

    llm = llm_client or get_llm_client()
    reg = _reg()

    raw = _call_expansion_llm(llm, q, reg, timeout)
    if raw is None:
        return _degraded()
    try:
        data = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError as e:
        logger.warning(
            "query_expand bad-JSON for %r: %s; raw=%r", _loggable(q), e, _loggable(raw[:200])
        )
        return _degraded()

    result = _parse_expansion(data, reg)
    with _lock:
        _cache.set(q, result)
    return result
