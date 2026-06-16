"""Coverage tests for backend.llm_client — all backends mocked, no network.

Each backend (`ollama`/`gemini`/`openrouter`/`anthropic`) does its SDK import
INSIDE the function, so we inject fake modules into sys.modules before calling.
time.sleep is no-op'd so retry/backoff loops run instantly.
"""

from __future__ import annotations

import sys
import types

import pytest

import backend.llm_client as L
from backend.config import settings


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(L.time, "sleep", lambda *_a, **_k: None)


# ── pure helpers ────────────────────────────────────────────────────────────


def test_strip_think():
    assert L.strip_think("<think>reason</think>answer") == "answer"
    assert L.strip_think("plain") == "plain"


def test_strip_json_fence_variants():
    assert L.strip_json_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert L.strip_json_fence('```\n{"a":1}\n```') == '{"a":1}'
    # no newline form
    assert L.strip_json_fence('```{"a":1}').startswith("{")
    assert L.strip_json_fence('{"a":1}') == '{"a":1}'


def test_select_model_per_backend(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_primary_model", "claude-x")
    monkeypatch.setattr(settings, "gemini_primary_model", "gem-x")
    monkeypatch.setattr(settings, "openrouter_primary_model", "or-x")
    monkeypatch.setattr(settings, "primary_model", "qwen-x")
    assert L.select_model("anthropic") == "claude-x"
    assert L.select_model("gemini") == "gem-x"
    assert L.select_model("openrouter") == "or-x"
    assert L.select_model("ollama") == "qwen-x"
    assert L.select_model("unknown-backend") == "qwen-x"


def test_has_api_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "g")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "a")
    assert L._has_api_key("gemini") is True
    assert L._has_api_key("openrouter") is False
    assert L._has_api_key("anthropic") is True
    assert L._has_api_key("ollama") is True  # no key needed


# ── assert_ready ────────────────────────────────────────────────────────────


def test_assert_ready_missing_keys(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    with pytest.raises(RuntimeError, match="anthropic"):
        L.assert_ready("anthropic")
    with pytest.raises(RuntimeError, match="gemini"):
        L.assert_ready("gemini")
    with pytest.raises(RuntimeError, match="openrouter"):
        L.assert_ready("openrouter")
    # ollama never raises
    L.assert_ready("ollama")


def test_assert_ready_with_keys(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "a")
    monkeypatch.setattr(settings, "gemini_api_key", "g")
    monkeypatch.setattr(settings, "openrouter_api_key", "o")
    L.assert_ready("anthropic")
    L.assert_ready("gemini")
    L.assert_ready("openrouter")


# ── circuit breaker (re-covered so this file is self-sufficient) ─────────────


def test_circuit_breaker_lifecycle(monkeypatch):
    monkeypatch.setattr(settings, "tagging_cloud_backend", "gemini")
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    monkeypatch.setattr(settings, "tagging_cloud_cooldown_s", 300)
    L._cloud_circuit.record_success()
    assert L.cloud_tagging_available() is True
    assert L.select_tagging_backend() == "gemini"
    status = L._cloud_circuit.status()
    assert status["open"] is False

    # cloud call fell back → failure → open
    L.note_tagging_outcome("gemini", fell_back=True)
    assert L._cloud_circuit.is_open() is True
    assert L.select_tagging_backend() == "ollama"
    assert L._cloud_circuit.status()["cooldown_remaining_s"] > 0

    # half-open after cooldown
    L._cloud_circuit._failed_at -= 301
    assert L._cloud_circuit.is_open() is False

    # success closes
    L.note_tagging_outcome("gemini", fell_back=True)
    L.note_tagging_outcome("gemini", fell_back=False)
    assert L._cloud_circuit.is_open() is False


def test_local_primary_outcome_ignored(monkeypatch):
    monkeypatch.setattr(settings, "tagging_cloud_backend", "ollama")
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    L._cloud_circuit.record_success()
    L.note_tagging_outcome("ollama", fell_back=True)
    assert L._cloud_circuit.is_open() is False


def test_cloud_unavailable_without_backend(monkeypatch):
    monkeypatch.setattr(settings, "tagging_cloud_backend", "")
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    assert L.cloud_tagging_available() is False
    assert L.select_tagging_backend() == "ollama"


# ── _dispatch routing + unknown backend ──────────────────────────────────────


def test_dispatch_unknown_backend():
    with pytest.raises(ValueError, match="unknown llm_backend"):
        L._dispatch("nope", "m", "s", "u", None, 1.0)


# ── ollama backend ───────────────────────────────────────────────────────────


def _install_fake_ollama(monkeypatch, *, content="hi", capture=None):
    mod = types.ModuleType("ollama")

    class _Client:
        def __init__(self, host=None, timeout=None):
            pass

        def chat(self, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            return {"message": {"content": content}}

    mod.Client = _Client
    monkeypatch.setitem(sys.modules, "ollama", mod)


def test_call_ollama_strips_think(monkeypatch):
    _install_fake_ollama(monkeypatch, content="<think>x</think>result")
    out = L._call_ollama("m", "sys", "user", None, timeout=5.0)
    assert out == "result"


def test_call_ollama_schema_appends_instruction(monkeypatch):
    cap: dict = {}
    _install_fake_ollama(monkeypatch, content="{}", capture=cap)
    L._call_ollama("m", "sys", "user", {"type": "object"}, timeout=5.0)
    sys_msg = cap["messages"][0]["content"]
    assert "/no_think" in sys_msg
    assert "JSON" in sys_msg


# ── gemini backend ───────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _httpx_mod().HTTPStatusError("err")

    def json(self):
        return self._json


def _httpx_mod():
    return sys.modules["httpx"]


def _install_fake_httpx(monkeypatch, *, responses=None, raise_kind=None):
    """responses: list of _FakeResp returned in sequence on .post().
    raise_kind: 'timeout' or 'http' → raise the module's own exception class."""
    mod = types.ModuleType("httpx")

    class HTTPError(Exception):
        pass

    class TimeoutException(HTTPError):
        pass

    class HTTPStatusError(HTTPError):
        pass

    class _Client:
        def __init__(self, timeout=None):
            self._i = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, params=None, json=None):
            if raise_kind == "timeout":
                raise TimeoutException("slow")
            if raise_kind == "http":
                raise HTTPError("conn refused")
            r = responses[min(self._i, len(responses) - 1)]
            self._i += 1
            return r

    mod.Client = _Client
    mod.HTTPError = HTTPError
    mod.TimeoutException = TimeoutException
    mod.HTTPStatusError = HTTPStatusError
    monkeypatch.setitem(sys.modules, "httpx", mod)


def test_call_gemini_success(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    monkeypatch.setattr(settings, "cloud_call_timeout_s", 50)
    data = {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}
    _install_fake_httpx(monkeypatch, responses=[_FakeResp(200, data)])
    out = L._call_gemini("m", "s", "u", {"type": "object"}, timeout=120.0)
    assert out == "answer"


def test_call_gemini_malformed_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    _install_fake_httpx(monkeypatch, responses=[_FakeResp(200, {"nope": 1})])
    assert L._call_gemini("m", "s", "u", None, timeout=10.0) == ""


def test_call_gemini_429_then_success(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    data = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    _install_fake_httpx(monkeypatch, responses=[_FakeResp(429), _FakeResp(200, data)])
    assert L._call_gemini("m", "s", "u", None, timeout=10.0) == "ok"


def test_call_gemini_429_exhausted(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    _install_fake_httpx(monkeypatch, responses=[_FakeResp(429)])
    with pytest.raises(L.LLMRateLimitError, match="rate limit"):
        L._call_gemini("m", "s", "u", None, timeout=10.0)


def test_call_gemini_timeout_mapped(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    _install_fake_httpx(monkeypatch, raise_kind="timeout")
    with pytest.raises(L.LLMRateLimitError, match="timeout"):
        L._call_gemini("m", "s", "u", None, timeout=10.0)


def test_call_gemini_http_error_mapped(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    _install_fake_httpx(monkeypatch, raise_kind="http")
    with pytest.raises(L.LLMRateLimitError, match="unavailable"):
        L._call_gemini("m", "s", "u", None, timeout=10.0)


# ── openrouter backend ───────────────────────────────────────────────────────


def _make_chunk(content=None, finish=None):
    delta = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(delta=delta, finish_reason=finish)
    return types.SimpleNamespace(choices=[choice])


def _install_fake_openai(monkeypatch, *, chunks=None, raise_kind=None):
    """raise_kind: 'rate'|'timeout'|'api' → raise the module's own exception."""
    mod = types.ModuleType("openai")

    class APIError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class _Completions:
        def create(self, **kwargs):
            if raise_kind == "rate":
                raise RateLimitError("429")
            if raise_kind == "timeout":
                raise APITimeoutError("slow")
            if raise_kind == "api":
                raise APIError("warming up")
            return iter(chunks or [])

    class _Chat:
        completions = _Completions()

    class OpenAI:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    mod.APIError = APIError
    mod.APITimeoutError = APITimeoutError
    mod.RateLimitError = RateLimitError
    mod.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return mod


def _or_settings(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "openrouter_api_base", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "cloud_call_timeout_s", 50)
    monkeypatch.setattr(settings, "openrouter_max_tokens", 100)
    monkeypatch.setattr(settings, "openrouter_referer", "")
    monkeypatch.setattr(settings, "openrouter_title", "T")
    monkeypatch.setattr(settings, "openrouter_use_response_format", True)


def test_call_openrouter_success(monkeypatch):
    _or_settings(monkeypatch)
    chunks = [_make_chunk("hel"), _make_chunk("lo", finish="stop"), _make_chunk(None)]
    _install_fake_openai(monkeypatch, chunks=chunks)
    out = L._call_openrouter("m", "s", "u", {"type": "object"}, timeout=120.0)
    assert out == "hello"


def test_call_openrouter_local_keeps_budget(monkeypatch):
    _or_settings(monkeypatch)
    monkeypatch.setattr(settings, "openrouter_api_base", "http://localhost:8080/v1")
    monkeypatch.setattr(settings, "openrouter_use_response_format", False)
    _install_fake_openai(monkeypatch, chunks=[_make_chunk("x", finish="stop")])
    assert L._call_openrouter("m", "s", "u", None, timeout=999.0) == "x"


def test_call_openrouter_empty_then_retry_success(monkeypatch):
    _or_settings(monkeypatch)
    # First attempt: empty stream → RuntimeError → retry; second: content.
    calls = {"n": 0}

    def _create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return iter([_make_chunk(None, finish="length")])
        return iter([_make_chunk("ok", finish="stop")])

    mod = types.ModuleType("openai")

    class APIError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class OpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=_create))

    mod.APIError = APIError
    mod.APITimeoutError = APITimeoutError
    mod.RateLimitError = RateLimitError
    mod.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)

    out = L._call_openrouter("m", "s", "u", None, timeout=10.0)
    assert out == "ok"


def test_call_openrouter_rate_limit_mapped(monkeypatch):
    _or_settings(monkeypatch)
    _install_fake_openai(monkeypatch, raise_kind="rate")
    with pytest.raises(L.LLMRateLimitError, match="rate limit"):
        L._call_openrouter("m", "s", "u", None, timeout=10.0)


def test_call_openrouter_timeout_mapped(monkeypatch):
    _or_settings(monkeypatch)
    _install_fake_openai(monkeypatch, raise_kind="timeout")
    with pytest.raises(L.LLMRateLimitError, match="timeout"):
        L._call_openrouter("m", "s", "u", None, timeout=10.0)


def test_call_openrouter_retries_exhausted(monkeypatch):
    _or_settings(monkeypatch)
    _install_fake_openai(monkeypatch, raise_kind="api")
    with pytest.raises(RuntimeError, match="failed after retries"):
        L._call_openrouter("m", "s", "u", None, timeout=10.0)


# ── anthropic backend ────────────────────────────────────────────────────────


def _install_fake_anthropic(monkeypatch, *, text="resp", capture=None):
    mod = types.ModuleType("anthropic")

    class _Messages:
        def create(self, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            block = types.SimpleNamespace(text=text)
            return types.SimpleNamespace(content=[block])

    class Anthropic:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)


def test_call_anthropic(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    cap: dict = {}
    _install_fake_anthropic(monkeypatch, text="hello", capture=cap)
    out = L._call_anthropic("m", "sys", "user", json_only=True)
    assert out == "hello"
    assert "JSON object" in cap["system"]


def test_call_anthropic_no_text_attr(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    mod = types.ModuleType("anthropic")

    class _Messages:
        def create(self, **kwargs):
            return types.SimpleNamespace(content=[object()])  # no .text

    class Anthropic:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    assert L._call_anthropic("m", "s", "u", json_only=False) == ""


# ── call_llm dispatch + fallback chain ───────────────────────────────────────


def test_call_llm_populates_meta(monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    monkeypatch.setattr(L, "_dispatch", lambda *a, **k: "out")
    meta: dict = {}
    out = L.call_llm("s", "u", model="m", meta=meta)
    assert out == "out"
    assert meta == {"backend_used": "ollama", "model_used": "m", "fallback": False}


def test_call_llm_fallback_on_failure(monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    monkeypatch.setattr(settings, "llm_fallback_backend", "ollama")
    monkeypatch.setattr(settings, "llm_fallback_model", "qwen2.5:1.5b")
    calls = {"n": 0}

    def _disp(backend, model, *a):
        calls["n"] += 1
        if backend == "gemini":
            raise L.LLMRateLimitError("429")
        return "local-answer"

    monkeypatch.setattr(L, "_dispatch", _disp)
    meta: dict = {}
    out = L.call_llm("s", "u", model="gem", meta=meta)
    assert out == "local-answer"
    assert meta["fallback"] is True
    assert meta["backend_used"] == "ollama"


def test_call_llm_reraises_when_no_fallback(monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    # fb == primary backend → no fallback, re-raise
    monkeypatch.setattr(settings, "llm_fallback_backend", "ollama")
    monkeypatch.setattr(settings, "llm_fallback_model", "qwen2.5:1.5b")

    def _disp(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(L, "_dispatch", _disp)
    with pytest.raises(RuntimeError, match="boom"):
        L.call_llm("s", "u", model="m")


# ── DefaultLLMClient adapter / provider (ADR 0021) ───────────────────────────


def test_adapter_delegates_to_call_llm(monkeypatch):
    """The adapter's call_llm() forwards all args verbatim to the module function."""
    seen = {}

    def _fake(system, user, *, model, schema=None, timeout=120.0, backend=None, meta=None):
        seen["args"] = (system, user, model, schema, timeout, backend, meta)
        return "sentinel"

    monkeypatch.setattr(L, "call_llm", _fake)
    out = L.DefaultLLMClient().call_llm(
        "sys", "usr", model="m", schema={"x": 1}, timeout=5.0, backend="ollama", meta={}
    )
    assert out == "sentinel"
    assert seen["args"] == ("sys", "usr", "m", {"x": 1}, 5.0, "ollama", {})


def test_adapter_satisfies_protocol():
    from backend.interfaces import LLMClient

    assert isinstance(L.DefaultLLMClient(), LLMClient)


def test_get_llm_client_is_singleton():
    assert L.get_llm_client() is L.get_llm_client()
    assert isinstance(L.get_llm_client(), L.DefaultLLMClient)
