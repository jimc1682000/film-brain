"""Unit tests for FeedbackService (LLM + filesystem store mocked)."""

import pytest

from backend.models import FeedbackPage
from backend.services import feedback as fb
from backend.services.feedback import FeedbackService


class _FakeLLM:
    """LLMClient Protocol double wrapping a call function (ADR 0021 seam)."""

    def __init__(self, fn):
        self._fn = fn

    def call_llm(self, *a, **k):
        return self._fn(*a, **k)


def _page(**over) -> FeedbackPage:
    base = {
        "page_id": "tags/thriller",
        "kind": "tags",
        "title": "驚悚 reject rate 偏高",
        "status": "open",
        "consultant_validated": False,
        "confidence": 0.7,
        "sources": ["review_stats"],
        "body": "## 現況\n\nreject_rate 0.4。",
    }
    base.update(over)
    return FeedbackPage(**base)


# ── _parse_json ──────────────────────────────────────────────────────────────


def test_parse_json_plain():
    svc = FeedbackService()
    assert svc._parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_json_fence():
    svc = FeedbackService()
    out = svc._parse_json('```json\n{"a": 2}\n```')
    assert out == {"a": 2}


def test_parse_json_bare_fence():
    svc = FeedbackService()
    out = svc._parse_json('```\n{"b": 3}\n```')
    assert out == {"b": 3}


def test_parse_json_malformed_returns_empty():
    svc = FeedbackService()
    assert svc._parse_json("{not json") == {}


def test_parse_json_non_dict_returns_empty():
    svc = FeedbackService()
    assert svc._parse_json("[1, 2, 3]") == {}


# ── _build_user_prompt ───────────────────────────────────────────────────────


def test_build_user_prompt_with_instruction():
    svc = FeedbackService()
    prompt = svc._build_user_prompt(_page(), "請驗證最新數據")
    assert "請驗證最新數據" in prompt
    assert "tags/thriller" in prompt
    assert "PAGE BODY" in prompt


def test_build_user_prompt_empty_instruction_uses_default():
    svc = FeedbackService()
    prompt = svc._build_user_prompt(_page(), "   ")
    assert "no editor instruction" in prompt


# ── execute ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_rejects_unsupported_op():
    svc = FeedbackService()
    with pytest.raises(NotImplementedError, match="ingest"):
        await svc.execute({"op": "ingest", "page_id": "x"})


@pytest.mark.asyncio
async def test_execute_reanalyze_happy_path(monkeypatch):
    fake = _FakeLLM(
        lambda system, user, **kw: (
            '{"frontmatter_updates": {"status": "dismissed"},'
            ' "body_section_title": "Validation", "body_section_md": "已確認不需處理"}'
        )
    )
    svc = FeedbackService(llm_client=fake)
    updated = _page(status="dismissed", consultant_validated=True)

    monkeypatch.setattr(fb, "get_page", lambda pid: _page())
    monkeypatch.setattr(fb, "select_model", lambda: "qwen2.5:1.5b")

    captured = {}

    def _fake_apply(**kwargs):
        captured.update(kwargs)
        return updated

    monkeypatch.setattr(fb, "apply_reanalyze", _fake_apply)

    out = await svc.execute({"op": "reanalyze", "page_id": "tags/thriller", "prompt": "這件不做"})

    assert out["page_id"] == "tags/thriller"
    assert out["frontmatter_updates"] == {"status": "dismissed"}
    assert out["body_section_title"] == "Validation"
    assert out["body_section_md"] == "已確認不需處理"
    assert out["model_used"] == "qwen2.5:1.5b"
    assert out["page"]["status"] == "dismissed"
    # apply_reanalyze got the parsed values
    assert captured["page_id"] == "tags/thriller"
    assert captured["model_used"] == "qwen2.5:1.5b"
    assert captured["frontmatter_updates"] == {"status": "dismissed"}


@pytest.mark.asyncio
async def test_execute_defaults_to_reanalyze_op(monkeypatch):
    # malformed LLM output → _parse_json returns {} → all fields fall back to defaults
    svc = FeedbackService(llm_client=_FakeLLM(lambda system, user, **kw: "garbage not json"))
    monkeypatch.setattr(fb, "get_page", lambda pid: _page())
    monkeypatch.setattr(fb, "select_model", lambda: "m")
    monkeypatch.setattr(fb, "apply_reanalyze", lambda **kw: _page())

    out = await svc.execute({"page_id": "tags/thriller"})  # no op key
    assert out["frontmatter_updates"] == {}
    assert out["body_section_title"] == ""
    assert out["body_section_md"] == ""


@pytest.mark.asyncio
async def test_reanalyze_missing_page_raises(monkeypatch):
    svc = FeedbackService()
    monkeypatch.setattr(fb, "get_page", lambda pid: None)
    with pytest.raises(FileNotFoundError, match="feedback page not found"):
        await svc.execute({"op": "reanalyze", "page_id": "tags/ghost"})


def test_name_property():
    assert FeedbackService().name == "feedback"
