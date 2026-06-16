"""Unit tests for the LLM-as-judge (LLM injected as a fake, cache → tmp)."""

import json

import pytest

from backend.services import eval_judge as ej


class _FakeLLM:
    """LLMClient Protocol double wrapping a call function (ADR 0021 seam)."""

    def __init__(self, fn):
        self._fn = fn

    def call_llm(self, *a, **k):
        return self._fn(*a, **k)


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ej, "_CACHE_PATH", tmp_path / "judge-cache.json")
    monkeypatch.setattr(ej, "_cache", None)
    yield
    monkeypatch.setattr(ej, "_cache", None)


_FILM = {
    "film_id": "mock-001",
    "title_zh": "笑園驚魂夜",
    "title_en": "Laugh Manor",
    "description": "喜劇",
    "tag_labels": ["comedy"],
}


def test_film_text_pure():
    t = ej._film_text(_FILM)
    assert "笑園驚魂夜" in t and "comedy" in t and "簡介" in t


def test_load_missing_file_returns_empty():
    assert ej._load() == {}


def test_judge_returns_score():
    fake = _FakeLLM(lambda *a, **k: json.dumps({"score": 2}))
    assert ej.judge("好笑的片", _FILM, llm_client=fake) == 2


def test_judge_clamps_out_of_range():
    fake = _FakeLLM(lambda *a, **k: json.dumps({"score": 9}))
    assert ej.judge("q", _FILM, llm_client=fake) == 2  # clamped to max 2


def test_judge_caches_and_persists():
    calls = {"n": 0}

    def _once(*a, **k):
        calls["n"] += 1
        return json.dumps({"score": 1})

    fake = _FakeLLM(_once)
    assert ej.judge("q", _FILM, llm_client=fake) == 1
    assert ej.judge("q", _FILM, llm_client=fake) == 1  # cache hit, no 2nd call
    assert calls["n"] == 1
    assert ej._CACHE_PATH.exists()  # persisted


def test_judge_empty_then_none():
    fake = _FakeLLM(lambda *a, **k: "")
    assert ej.judge("q", _FILM, retries=1, llm_client=fake) is None


def test_judge_parse_failure_none():
    fake = _FakeLLM(lambda *a, **k: "not json")
    assert ej.judge("q", _FILM, retries=0, llm_client=fake) is None


def test_judge_llm_exception_none():
    def _boom(*a, **k):
        raise RuntimeError("down")

    assert ej.judge("q", _FILM, retries=0, llm_client=_FakeLLM(_boom)) is None
