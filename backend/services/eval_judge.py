"""LLM-as-judge for search evaluation (ADR 0004).

Given a query and a film, the judge returns a graded relevance score
(0 = irrelevant, 1 = partial, 2 = highly relevant). This is the ground-truth
oracle for the eval harness — no human labels, no traffic needed. Scores are
cached to disk (data/eval-judge-cache.json) keyed by query+film so repeated
eval/tune runs don't re-pay the LLM cost.

NB (ADR 0004): the judge is self-referential — optimising to it is not the
same as true accuracy. Sample-check the judge against human judgement
occasionally.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from backend.config import settings
from backend.llm_client import get_llm_client, select_model, strip_json_fence

if TYPE_CHECKING:
    from backend.interfaces import LLMClient

_CACHE_PATH = settings.db_path.parent / "eval-judge-cache.json"
_cache: dict | None = None
_lock = threading.Lock()

_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "integer"}},
    "required": ["score"],
}

_SYSTEM = (
    "你是電影『推薦』的相關性評審。使用者常是想到一個情境/心情/主題就來找片"
    "(例:『分手療傷』『適合下雨天』),不是要字面命中。請評這部片**能不能滿足"
    "使用者這個情境/心情的需求**,而非是否字面講到那件事。"
    '只回 JSON {"score": n}:'
    "2=很對味,使用者大概率會滿意;"
    "1=沾到邊、勉強可推;"
    "0=完全不對味。"
    "範例:查『分手療傷』→ 一部溫馨療癒/走出傷痛的片即使沒演『分手』也算 2;"
    "一部熱血動作片則是 0。別只看字面,看意圖與氛圍是否吻合。"
    "特別注意:查詢越模糊/口語(例『想看點特別的』『下雨天的心情』),"
    "使用者要的是『對味 + 一點意外驚喜』,不是精準命中 —— 氛圍相合、能帶來"
    "discovery 的片該給 1~2,不要因為『沒完全照字面』就打 0。"
)


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    assert _cache is not None  # set by the block above
    return _cache


def _film_text(film: dict) -> str:
    desc = (film.get("description") or film.get("tmdb_overview") or "")[:300]
    labels = film.get("tag_labels") or []
    return (
        f"片名:{film.get('title_zh', '')} {film.get('title_en') or ''}\n"
        f"簡介:{desc}\n"
        f"標籤:{', '.join(labels)}"
    )


def judge(
    query: str,
    film: dict,
    *,
    timeout: float = 90.0,
    retries: int = 1,
    llm_client: LLMClient | None = None,
) -> int | None:
    """Graded relevance 0/1/2, cached. Returns None if the LLM call fails.

    Timeout is generous because the judge may be a local thinking model
    (Qwen3) whose reasoning precedes the answer — cutting the connection early
    both loses the answer AND jams a single-slot server, so we wait it out.
    Retries once on empty/parse failure (thinking models occasionally emit no
    final content)."""
    key = f"{query}|{film.get('film_id')}"
    cache = _load()
    if key in cache:
        return cache[key]
    llm = llm_client or get_llm_client()
    user = f"查詢:{query}\n\n{_film_text(film)}"
    for _ in range(retries + 1):
        try:
            raw = llm.call_llm(
                _SYSTEM, user, model=select_model(), schema=_SCHEMA, timeout=timeout, meta={}
            )
            if not raw or not raw.strip():
                continue  # thinking ate the budget — retry
            score = max(0, min(2, int(json.loads(strip_json_fence(raw)).get("score", 0))))
        except Exception:
            continue
        with _lock:
            cache[key] = score
            _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        return score
    return None
