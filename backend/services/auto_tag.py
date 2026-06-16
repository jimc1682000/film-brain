"""AutoTagService — LLM-powered film tagging.

Backend dispatch + per-provider HTTP plumbing lives in
`backend/llm_client.py`. This module owns the domain layer: build the
film prompt, run a single LLM pass, and validate suggestions against the
taxonomy. Re-analyze is just a fresh call to the same model.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from backend.llm_client import (
    get_llm_client,
    note_tagging_outcome,
    select_model,
    select_tagging_backend,
    strip_think,
)
from backend.models import AutoTagResponse, TagSuggestion
from backend.services.base import BaseService
from backend.tag_registry import TagRegistry

if TYPE_CHECKING:
    from backend.interfaces import LLMClient

# Locale -> human-readable language name used inside the LLM system prompt.
_LOCALE_LABELS = {
    "zh_TW": "Traditional Chinese (繁體中文)",
    "zh_CN": "Simplified Chinese (简体中文)",
    "en": "English",
    "ja": "Japanese (日本語)",
    "id": "Bahasa Indonesia",
}


def build_system_prompt(locale: str = "zh_TW") -> str:
    """Render the tagging system prompt for a given UI locale.

    reasoning 輸出語言跟 UI 一致, 由 caller 傳 locale 決定.
    """
    lang = _LOCALE_LABELS.get(locale, locale)
    return f"""You are a film classification expert for CATCHPLAY+ streaming platform.
Given a film's metadata, suggest relevant tags from the provided taxonomy.

Rules:
1. ONLY use tag_ids from the provided taxonomy
2. Each tag must include a confidence score (0.0-1.0)
3. Provide reasoning for each tag — MUST be written in {lang}
4. Suggest 5-15 tags across multiple dimensions
5. Focus on the most distinctive and useful tags for content discovery

Output format: JSON array of objects with fields:
- tag_id: string (must exist in taxonomy)
- dimension: string
- confidence: float (0.0-1.0)
- reasoning: string (brief, 1 sentence, in {lang})

IMPORTANT: the `reasoning` field MUST be written in {lang}, matching the UI locale ({locale}). Do not use any other language.
"""


# Default prompt kept for backwards-compat (tests / external callers).
SYSTEM_PROMPT = build_system_prompt("zh_TW")


OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["tag_id", "dimension", "confidence", "reasoning"],
        "properties": {
            "tag_id": {"type": "string"},
            "dimension": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string"},
        },
    },
}


class AutoTagService(BaseService):
    """Domain layer for LLM tagging — dispatch lives in backend.llm_client."""

    def __init__(self, llm_client: LLMClient | None = None):
        self._registry = TagRegistry()
        self._taxonomy_context = self._registry.to_prompt_context()
        # ADR 0021 injection seam — defaults to the process-wide client.
        self._llm = llm_client or get_llm_client()

    @property
    def name(self) -> str:
        return "auto_tag"

    async def execute(self, input_data: dict) -> dict:
        film = input_data["film"]
        film_id = film["film_id"]
        locale = input_data.get("locale", "zh_TW")
        system_prompt = build_system_prompt(locale)

        # Prefer a cloud model when its circuit is healthy; otherwise tag
        # locally. The cloud→local failover (and the circuit bookkeeping below)
        # is handled by call_llm + note_tagging_outcome.
        backend = select_tagging_backend()
        model = select_model(backend)
        meta: dict = {}
        suggestions = self._llm_tag(backend, model, film, system_prompt, meta)
        suggestions = self._validate_suggestions(suggestions)

        # Feed the outcome back into the cloud circuit breaker: a cloud call
        # that fell back to local opens it (skip cloud next time), a clean cloud
        # call closes it.
        fell_back = meta.get("fallback", False)
        note_tagging_outcome(backend, fell_back=fell_back)

        # When the cloud model failed and the local fallback served the request,
        # report the model that actually ran + a UI warning.
        model_used = meta.get("model_used", model) if fell_back else model
        warning = None
        if fell_back:
            warning = (
                "雲端模型暫不可用,已改用本地模型 " + meta.get("model_used", "") + " (結果較簡略)"
            )

        return AutoTagResponse(
            film_id=film_id,
            title=film.get("title_zh", ""),
            suggestions=suggestions,
            model_used=model_used,
            timestamp=datetime.now(),
            warning=warning,
        ).model_dump()

    def _build_film_prompt(self, film: dict) -> str:
        parts = [
            f"Title (ZH): {film.get('title_zh', '')}",
            f"Title (EN): {film.get('title_en', '')}",
            f"Description: {film.get('description', '')}",
        ]
        if film.get("tmdb_overview"):
            parts.append(f"TMDb Overview: {film['tmdb_overview']}")
        if film.get("tmdb_genres"):
            parts.append(f"TMDb Genres: {film['tmdb_genres']}")
        if film.get("tmdb_keywords"):
            parts.append(f"TMDb Keywords: {film['tmdb_keywords']}")
        if film.get("tmdb_cast"):
            parts.append(f"Cast: {film['tmdb_cast']}")
        if film.get("tmdb_director"):
            parts.append(f"Director: {film['tmdb_director']}")
        if film.get("original_genre"):
            parts.append(f"Original Genre: {film['original_genre']}")
        return "\n".join(parts)

    def _llm_tag(
        self,
        backend: str,
        model: str,
        film: dict,
        system_prompt: str,
        meta: dict | None = None,
    ) -> list[TagSuggestion]:
        user_prompt = (
            f"{self._taxonomy_context}\n\n"
            f"---\nFILM TO TAG:\n{self._build_film_prompt(film)}\n\n"
            f"Return a JSON array of tag suggestions."
        )
        # 180s, not the 120s default: a local model must first eval the full
        # ~2.5k-token taxonomy prompt (~40s on the CPU box) before generating,
        # so a tagging call legitimately needs more headroom than a query.
        text = self._llm.call_llm(
            system_prompt,
            user_prompt,
            model=model,
            schema=OUTPUT_SCHEMA,
            timeout=180.0,
            backend=backend,
            meta=meta,
        )
        return self._parse_response(text)

    @staticmethod
    def _extract_tag_dicts(text: str) -> list:
        """Pull the tag array out of raw LLM text: strip think/fences, parse JSON,
        and unwrap the common shapes. Returns [] on parse failure.

        Some models wrap the array in {"tags": [...]} / {"suggestions": [...]};
        backends in json_object mode (OpenRouter) are forced to emit one
        top-level object, so a single {tag_id, dimension} → a one-element list
        instead of dropping everything.
        """
        text = strip_think(text).strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            for key in ("tags", "suggestions", "items"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            if "tag_id" in data or "dimension" in data:
                return [data]
            return []
        return data

    def _parse_response(self, text: str) -> list[TagSuggestion]:
        data = self._extract_tag_dicts(text)
        suggestions = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            # Small local models (e.g. qwen2.5:1.5b) reliably pick the right
            # tags but SWAP the fields — writing the dimension name into
            # `tag_id` and the actual tag into `dimension`. Resolve
            # orientation-agnostically: whichever field the registry
            # recognises as a real tag IS the tag_id; drop the item only if
            # neither does. The dimension + labels then come from the
            # registry, never the model, so they are always self-consistent.
            raw_id = str(item.get("tag_id", ""))
            raw_dim = str(item.get("dimension", ""))
            tag = self._registry.get_tag(raw_id) or self._registry.get_tag(raw_dim)
            if not tag:
                continue
            tag_id = tag["tag_id"]
            if tag_id in seen:
                continue
            seen.add(tag_id)
            try:
                labels = tag.get("labels", {})
                suggestions.append(
                    TagSuggestion(
                        tag_id=tag_id,
                        dimension=tag["dimension"],
                        label_zh_tw=labels.get("zh_TW", ""),
                        label_en=labels.get("en", ""),
                        confidence=min(max(float(item.get("confidence", 0.5)), 0.0), 1.0),
                        reasoning=item.get("reasoning", ""),
                    )
                )
            except (KeyError, ValueError):
                continue
        return suggestions

    def _validate_suggestions(self, suggestions: list[TagSuggestion]) -> list[TagSuggestion]:
        valid_ids = self._registry.all_tag_ids
        return [s for s in suggestions if s.tag_id in valid_ids]
