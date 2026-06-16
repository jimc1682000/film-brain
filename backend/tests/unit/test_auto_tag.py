"""Unit tests for AutoTagService (LLM + circuit-breaker mocked).

The taxonomy (TagRegistry) is real — it loads the shipped dimension-mapping.json
— but every network/LLM call is patched so the suggestion + validation flow runs
deterministically.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.services import auto_tag as at
from backend.services.auto_tag import AutoTagService, build_system_prompt


class _FakeLLM:
    """LLMClient Protocol double wrapping a call function (ADR 0021 seam)."""

    def __init__(self, fn):
        self._fn = fn

    def call_llm(self, *a, **k):
        return self._fn(*a, **k)


@pytest.fixture
def service():
    return AutoTagService()


def _real_tag_id(service: AutoTagService) -> str:
    """A tag_id that genuinely exists in the loaded taxonomy."""
    return next(iter(service._registry.all_tag_ids))


# ── build_system_prompt ─────────────────────────────────────────────────────


def test_build_system_prompt_known_locale():
    p = build_system_prompt("en")
    assert "English" in p and "zh_TW" not in p


def test_build_system_prompt_unknown_locale_passthrough():
    p = build_system_prompt("xx_YY")
    assert "xx_YY" in p


# ── _build_film_prompt ──────────────────────────────────────────────────────


def test_build_film_prompt_minimal(service):
    prompt = service._build_film_prompt({"title_zh": "片名"})
    assert "Title (ZH): 片名" in prompt
    assert "TMDb Overview" not in prompt


def test_build_film_prompt_full(service):
    prompt = service._build_film_prompt(
        {
            "title_zh": "片名",
            "title_en": "Name",
            "description": "說明",
            "tmdb_overview": "overview",
            "tmdb_genres": "Drama",
            "tmdb_keywords": "kw",
            "tmdb_cast": "Actor",
            "tmdb_director": "Dir",
            "original_genre": "劇情",
        }
    )
    for needle in (
        "TMDb Overview: overview",
        "TMDb Genres: Drama",
        "TMDb Keywords: kw",
        "Cast: Actor",
        "Director: Dir",
        "Original Genre: 劇情",
    ):
        assert needle in prompt


# ── _parse_response branches ────────────────────────────────────────────────


def test_parse_response_invalid_json_returns_empty(service):
    assert service._parse_response("not json at all") == []


def test_parse_response_json_fence(service, monkeypatch):
    monkeypatch.setattr(at, "strip_think", lambda t: t)
    tid = _real_tag_id(service)
    payload = json.dumps([{"tag_id": tid, "dimension": "x", "confidence": 0.8, "reasoning": "r"}])
    out = service._parse_response(f"```json\n{payload}\n```")
    assert len(out) == 1 and out[0].tag_id == tid


def test_parse_response_bare_fence(service):
    tid = _real_tag_id(service)
    payload = json.dumps([{"tag_id": tid, "dimension": "x", "confidence": 0.5, "reasoning": ""}])
    out = service._parse_response(f"```\n{payload}\n```")
    assert len(out) == 1


def test_parse_response_dict_wrapper(service):
    tid = _real_tag_id(service)
    body = {"tags": [{"tag_id": tid, "dimension": "x", "confidence": 0.5, "reasoning": ""}]}
    out = service._parse_response(json.dumps(body))
    assert len(out) == 1 and out[0].tag_id == tid


def test_parse_response_single_object_no_wrapper(service):
    tid = _real_tag_id(service)
    body = {"tag_id": tid, "dimension": "x", "confidence": 0.5, "reasoning": ""}
    out = service._parse_response(json.dumps(body))
    assert len(out) == 1


def test_parse_response_unknown_dict_returns_empty(service):
    # dict with no tags/suggestions/items key and no tag_id/dimension → []
    out = service._parse_response(json.dumps({"foo": "bar"}))
    assert out == []


def test_parse_response_field_swap(service):
    # qwen-style swap: dimension name in tag_id, real tag in dimension.
    tid = _real_tag_id(service)
    item = {"tag_id": "not-a-real-tag", "dimension": tid, "confidence": 0.9, "reasoning": "r"}
    out = service._parse_response(json.dumps([item]))
    assert len(out) == 1 and out[0].tag_id == tid


def test_parse_response_dedup_and_clamp(service):
    tid = _real_tag_id(service)
    items = [
        {"tag_id": tid, "dimension": "x", "confidence": 5.0, "reasoning": "first"},
        {"tag_id": tid, "dimension": "x", "confidence": 0.3, "reasoning": "dup"},
        {"tag_id": "totally-unknown", "dimension": "nope", "confidence": 0.5, "reasoning": ""},
        "not-a-dict",
    ]
    out = service._parse_response(json.dumps(items))
    assert len(out) == 1  # dedup drops the second, unknown + non-dict dropped
    assert out[0].confidence == 1.0  # clamped from 5.0


def test_parse_response_strips_think(service, monkeypatch):
    # strip_think removes <think> blocks before JSON parse.
    tid = _real_tag_id(service)
    payload = json.dumps([{"tag_id": tid, "dimension": "x", "confidence": 0.5, "reasoning": ""}])
    out = service._parse_response(f"<think>reasoning</think>{payload}")
    assert len(out) == 1


# ── _validate_suggestions ───────────────────────────────────────────────────


def test_validate_suggestions_filters_unknown(service):
    from backend.models import TagSuggestion

    tid = _real_tag_id(service)
    sugg = [
        TagSuggestion(tag_id=tid, dimension="genre", confidence=0.5),
        TagSuggestion(tag_id="ghost-tag", dimension="genre", confidence=0.5),
    ]
    out = service._validate_suggestions(sugg)
    assert [s.tag_id for s in out] == [tid]


# ── execute (full flow) ─────────────────────────────────────────────────────


def _stub_dispatch(service, monkeypatch, *, fell_back: bool, response_text: str):
    """Stub the llm_client helpers + inject a fake LLM via the service's seam."""
    monkeypatch.setattr(at, "select_tagging_backend", lambda: "cloud")
    monkeypatch.setattr(at, "select_model", lambda backend: "gemini-test")
    monkeypatch.setattr(at, "strip_think", lambda t: t)

    notes = {}
    monkeypatch.setattr(
        at, "note_tagging_outcome", lambda backend, *, fell_back: notes.update(fell_back=fell_back)
    )

    def _fake_call_llm(system, user, *, model, schema, timeout, backend, meta):
        if fell_back:
            meta["fallback"] = True
            meta["model_used"] = "qwen2.5:1.5b"
        return response_text

    service._llm = _FakeLLM(_fake_call_llm)
    return notes


def test_execute_clean_cloud(service, monkeypatch):
    tid = _real_tag_id(service)
    resp = json.dumps([{"tag_id": tid, "dimension": "x", "confidence": 0.7, "reasoning": "r"}])
    notes = _stub_dispatch(service, monkeypatch, fell_back=False, response_text=resp)
    result = asyncio.run(service.execute({"film": {"film_id": "f1", "title_zh": "片"}}))
    assert result["film_id"] == "f1"
    assert result["title"] == "片"
    assert result["model_used"] == "gemini-test"
    assert result["warning"] is None
    assert notes == {"fell_back": False}
    assert any(s["tag_id"] == tid for s in result["suggestions"])


def test_execute_fallback_to_local(service, monkeypatch):
    tid = _real_tag_id(service)
    resp = json.dumps([{"tag_id": tid, "dimension": "x", "confidence": 0.4, "reasoning": "r"}])
    notes = _stub_dispatch(service, monkeypatch, fell_back=True, response_text=resp)
    result = asyncio.run(
        service.execute({"film": {"film_id": "f2", "title_zh": "片2"}, "locale": "en"})
    )
    assert result["model_used"] == "qwen2.5:1.5b"
    assert result["warning"] is not None and "qwen2.5:1.5b" in result["warning"]
    assert notes == {"fell_back": True}


def test_name_property(service):
    assert service.name == "auto_tag"
