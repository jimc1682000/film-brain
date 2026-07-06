"""Unified LLM dispatch for ollama / anthropic / gemini.

The two skills that talk to LLMs (`auto_tag`, `feedback`) each reimplemented
the same three call_X helpers, the same backend-to-model resolution, and
the same 'fail loudly if api key missing' check. The router layer then
reimplemented the readiness check a third time. This module collapses
all of that into one call site.

Public surface:

    select_model() -> str
        Returns the configured model id for the current settings.llm_backend.

    assert_ready(backend=None)
        Raises RuntimeError if the API key for the active backend is missing.
        Routers can call this in a 503 guard; skills should not need it
        because call_llm raises the same way on first use.

    call_llm(system, user, *, model, schema=None, timeout=120.0, backend=None) -> str
        Dispatches to the active backend, returns the raw response text.
        `schema` enables JSON-mode / responseSchema where the backend
        supports it; pass None for free-form text output.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from backend.config import settings

logger = logging.getLogger(__name__)


class LLMRateLimitError(RuntimeError):
    """Raised when the LLM provider returns 429 after our retries.

    Routers translate this into a 503 with a clear message instead of a
    bare 500, so a quota blip during a demo reads as 'try again shortly'.
    """


# Qwen3 / similar models emit <think>...</think> before the answer when
# thinking mode is on. Callers usually want it stripped — exposed as a
# helper so parsing stays in the calling skill but the regex lives here.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    """Drop any <think>...</think> blocks emitted by reasoning models."""
    return _THINK_BLOCK.sub("", text)


def strip_json_fence(text: str) -> str:
    """Strip a leading ``` / ```json fence and trailing ``` if present.

    Qwen3 thinking models habitually wrap structured output in markdown
    code fences even when the prompt says "Return ONLY valid JSON". With
    response_format=json_object the server's grammar would prevent this,
    but we toggle that off when the GBNF path is unstable (config flag).
    Strip the fences so json.loads still works.
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _active_backend(backend: str | None = None) -> str:
    return backend or settings.llm_backend


def select_model(backend: str | None = None) -> str:
    """Return the configured model id for the active backend.

    One model per backend — there is no consultant/escalation tier. Unknown
    backends fall back to the generic settings.primary_model (ollama default).
    """
    b = _active_backend(backend)
    if b == "anthropic":
        return settings.anthropic_primary_model
    if b == "gemini":
        return settings.gemini_primary_model
    if b == "openrouter":
        return settings.openrouter_primary_model
    return settings.primary_model


# --- Cloud tagging health gate ---------------------------------------------
# Auto-tag prefers a cloud model (the local CPU box can't run the full-taxonomy
# prompt usefully). The free cloud tier is flaky, so a circuit breaker skips it
# for a cooldown after any failure — no per-request retry wait — then half-opens:
# the next tagging call retries cloud and its outcome reopens or closes the
# circuit. No background poller, no quota burn.
class _CloudCircuit:
    def __init__(self) -> None:
        self._failed_at = 0.0

    @property
    def _cooldown(self) -> float:
        return float(settings.tagging_cloud_cooldown_s)

    def is_open(self) -> bool:
        """True → skip cloud (still cooling down from a recent failure)."""
        return (time.time() - self._failed_at) < self._cooldown

    def record_failure(self) -> None:
        self._failed_at = time.time()

    def record_success(self) -> None:
        self._failed_at = 0.0

    def status(self) -> dict:
        remaining = max(0.0, self._cooldown - (time.time() - self._failed_at))
        return {"open": self.is_open(), "cooldown_remaining_s": round(remaining)}


_cloud_circuit = _CloudCircuit()


def _has_api_key(backend: str) -> bool:
    if backend == "gemini":
        return bool(settings.gemini_api_key)
    if backend == "openrouter":
        return bool(settings.openrouter_api_key)
    if backend == "anthropic":
        return bool(settings.anthropic_api_key)
    return True  # ollama needs no key


def cloud_tagging_available() -> bool:
    """Cloud tagging backend configured, keyed, and not circuit-broken?"""
    cb = settings.tagging_cloud_backend
    return bool(cb) and _has_api_key(cb) and not _cloud_circuit.is_open()


def select_tagging_backend() -> str:
    """Backend for an auto-tag call: cloud when healthy, else local."""
    return settings.tagging_cloud_backend if cloud_tagging_available() else settings.llm_backend


def note_tagging_outcome(backend: str, *, fell_back: bool) -> None:
    """Feed an auto-tag call's result back into the circuit breaker.

    A cloud primary that fell back to local = a cloud failure (open the
    circuit); a cloud primary that served the call = healthy (close it). A
    local primary is not about cloud health, so it's ignored.
    """
    if backend == settings.tagging_cloud_backend and backend != settings.llm_backend:
        if fell_back:
            _cloud_circuit.record_failure()
        else:
            _cloud_circuit.record_success()


def assert_ready(backend: str | None = None) -> None:
    """Raise RuntimeError when the backend's API key is missing.

    Routers can call this in their lazy-init path so the HTTP 503 carries a
    clear message instead of a stack trace on first request.
    """
    b = _active_backend(backend)
    if b == "anthropic" and not settings.anthropic_api_key:
        raise RuntimeError("llm_backend=anthropic but ANTHROPIC_API_KEY missing.")
    if b == "gemini" and not settings.gemini_api_key:
        raise RuntimeError("llm_backend=gemini but GEMINI_API_KEY missing.")
    if b == "openrouter" and not settings.openrouter_api_key:
        raise RuntimeError("llm_backend=openrouter but OPENROUTER_API_KEY missing.")


def call_llm(
    system: str,
    user: str,
    *,
    model: str,
    schema: dict | None = None,
    timeout: float = 120.0,
    backend: str | None = None,
    meta: dict | None = None,
) -> str:
    """Issue a single LLM completion and return raw text.

    `schema` is the JSON output schema (Ollama `format=` / Gemini
    `responseSchema`). When None, response is treated as free-form text;
    anthropic does not support a hard schema, so a `Return ONLY a JSON
    object` instruction is appended to the system prompt automatically.

    Pass a `meta` dict to learn which backend actually served the call —
    it is populated with `backend_used` / `model_used` / `fallback` (bool).
    Callers surface `fallback=True` as a "Gemini throttled, used local
    model" warning in the UI.
    """
    b = _active_backend(backend)
    assert_ready(b)
    if meta is not None:
        meta.update(backend_used=b, model_used=model, fallback=False)
    try:
        return _dispatch(b, model, system, user, schema, timeout)
    except (LLMRateLimitError, RuntimeError) as e:
        # Any cloud failure — 429 quota, a delisted free slug (404), a
        # connection error, retries exhausted — falls back to the local model
        # if one is configured and we are not already on it. Keeps the demo
        # working instead of degrading whenever the free tier misbehaves.
        fb = settings.llm_fallback_backend
        if fb and fb != b and settings.llm_fallback_model:
            logger.warning("primary LLM (%s) failed (%s) — falling back to %s", b, e, fb)
            if meta is not None:
                meta.update(backend_used=fb, model_used=settings.llm_fallback_model, fallback=True)
            return _dispatch(fb, settings.llm_fallback_model, system, user, schema, timeout)
        raise


def _dispatch(
    backend: str,
    model: str,
    system: str,
    user: str,
    schema: dict | None,
    timeout: float,
) -> str:
    if backend == "ollama":
        return _call_ollama(model, system, user, schema, timeout)
    if backend == "gemini":
        return _call_gemini(model, system, user, schema, timeout)
    if backend == "openrouter":
        return _call_openrouter(model, system, user, schema, timeout)
    if backend == "anthropic":
        return _call_anthropic(model, system, user, schema is not None)
    raise ValueError(f"unknown llm_backend: {backend!r}")


# --- per-backend implementations ---------------------------------------


def _call_ollama(
    model: str, system: str, user: str, schema: dict[str, Any] | None, timeout: float = 120.0
) -> str:
    import ollama

    # Honor the caller's timeout so a cold model load (CPU ~86s) can't block past
    # query_expand's budget — it raises, query_expand degrades gracefully instead
    # of the whole request hard-timing-out at the HTTP layer.
    client = ollama.Client(host=settings.ollama_host, timeout=timeout)
    # `/no_think` is Qwen3's inline switch to suppress thinking output. Add
    # it both to system + user defensively — some Ollama builds ignore the
    # system-only form.
    # num_predict caps output length. Small CPU models (qwen2.5:1.5b) sometimes
    # ramble far past a complete answer; uncapped, a single auto-tag call can
    # run minutes and blow past the HTTP timeout (→ 500). A full tag set with
    # reasoning fits comfortably in ~800 tokens, so this bounds worst-case
    # latency without truncating legitimate output.
    options: dict[str, Any] = {"temperature": 0.2, "num_ctx": 8192, "num_predict": 800}
    # NOTE: do NOT pass format=schema here. Ollama turns the JSON schema into a
    # GBNF grammar; with our taxonomy enum it's huge and grammar-constrained
    # decoding on CPU crawls (minutes/query). The prompt already asks for JSON;
    # strip_json_fence + json.loads + taxonomy validation handle the output.
    sys_msg = system + "\n/no_think"
    if schema is not None:
        sys_msg += "\n只輸出一個合法 JSON 物件,不要任何說明或 markdown 圍欄。"
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user + "\n/no_think"},
        ],
        "options": options,
    }
    resp = client.chat(**kwargs)
    # strip any <think> a reasoning model leaks despite /no_think + format.
    return strip_think(resp["message"]["content"])


def _call_gemini(
    model: str, system: str, user: str, schema: dict[str, Any] | None, timeout: float
) -> str:
    import httpx

    url = f"{settings.gemini_api_base}/models/{model}:generateContent"
    generation_config: dict[str, Any] = {"temperature": 0.2}
    if schema is not None:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = schema
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": generation_config,
    }
    # Cloud answers in seconds; cap the HTTP timeout well below the caller's
    # (long, local-prompt-eval) budget so a hung/broken cloud fails fast and we
    # fall back to local instead of stalling the whole request.
    client_timeout = min(timeout, float(settings.cloud_call_timeout_s))
    # Free-tier Gemini is rate-limited (RPM + daily). Retry a couple of times
    # on 429 with linear backoff. Any OTHER failure — timeout, 5xx, connection
    # error — is converted to LLMRateLimitError so call_llm's fallback chain
    # catches it (it only catches LLMRateLimitError/RuntimeError, not bare
    # httpx.HTTPError) and the tagging circuit breaker opens. A timeout is not
    # retried: if it didn't answer once it won't on attempt two.
    with httpx.Client(timeout=client_timeout) as client:
        for attempt in range(3):
            try:
                resp = client.post(url, params={"key": settings.gemini_api_key}, json=payload)
                if resp.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
            except httpx.TimeoutException as e:
                raise LLMRateLimitError(f"gemini timeout after {client_timeout:.0f}s") from e
            except httpx.HTTPError as e:
                raise LLMRateLimitError(f"gemini unavailable: {type(e).__name__}") from e
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                return ""
    raise LLMRateLimitError(
        "Gemini API rate limit (429) — free-tier quota exhausted, retry shortly."
    )


def _build_openrouter_kwargs(
    model: str, system: str, user: str, schema: dict[str, Any] | None, client_timeout: float
) -> tuple[Any, dict[str, Any]]:
    """Build the OpenAI client + create() kwargs for an OpenRouter call."""
    from openai import OpenAI

    sys_prompt = system
    response_format: dict[str, Any] | None = None
    if schema is not None:
        response_format = {"type": "json_object"}
        sys_prompt = sys_prompt + "\n\nReturn ONLY valid JSON matching the requested shape."

    client = OpenAI(
        base_url=settings.openrouter_api_base,
        api_key=settings.openrouter_api_key,
        max_retries=2,
        timeout=client_timeout,
        default_headers={
            # OpenRouter asks for these for free-tier attribution; harmless if unset.
            "HTTP-Referer": settings.openrouter_referer,
            "X-Title": settings.openrouter_title,
        },
    )
    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        # Cap output. Thinking models that ignore the reasoning flag (e.g. a
        # local llama.cpp Qwen3 used as eval judge) spend tokens reasoning
        # before the answer — too small a budget leaves content empty.
        "max_tokens": settings.openrouter_max_tokens,
        "stream": True,
        # OpenRouter reasoning control via extra_body. Use exclude-only, NOT
        # enabled:false — some models (e.g. openai/gpt-oss-*) MANDATE reasoning
        # and 400 on "enabled: false" ("Reasoning is mandatory for this
        # endpoint"). `exclude: true` lets them reason but drops the CoT from
        # the response, and our stream collects only delta.content anyway, so
        # the answer stays clean either way.
        "extra_body": {"reasoning": {"exclude": True}},
    }
    if response_format is not None and settings.openrouter_use_response_format:
        # Server-side grammar enforcement. Disable via
        # OPENROUTER_USE_RESPONSE_FORMAT=false when the backend's GBNF path is
        # crashing (llama.cpp #18988 / #19008) — the appended "Return ONLY
        # valid JSON" instruction usually suffices to keep output well-formed,
        # and json.loads remains the floor at parse time.
        create_kwargs["response_format"] = response_format
    return client, create_kwargs


def _collect_openrouter_stream(stream) -> str:
    """Drain a streamed completion to its concatenated content.

    Raises RuntimeError on zero content deltas: lemonade's local Qwen3 thinking
    model can burn the whole token budget inside reasoning_content
    (reasoning:{enabled:false} can't disable it — lemonade #1511), so
    finish_reason='length' arrives with no answer. Raising makes it retryable +
    visible instead of degrading into a confusing 'json.loads("") char 0' later.
    """
    parts: list[str] = []
    last_finish: str | None = None
    for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if delta and delta.content:
            parts.append(delta.content)
        if choice.finish_reason:
            last_finish = choice.finish_reason
    if not parts:
        raise RuntimeError(f"empty content (no delta.content; finish_reason={last_finish})")
    return "".join(parts)


def _call_openrouter(
    model: str, system: str, user: str, schema: dict[str, Any] | None, timeout: float
) -> str:
    """OpenAI-compatible /chat/completions via the official `openai` SDK, streamed.

    We use the SDK (not hand-rolled httpx) so retries / backoff / streaming /
    cancellation match how mature agents drive the same endpoint (ADR 0007).

    Resilience comes in two layers:
      1. SDK `max_retries` retries transport-level failures (connection refused,
         timeouts, 5xx) with exponential backoff.
      2. Our own retry loop handles the case the SDK does NOT: lemonade returns
         HTTP 200 then a streamed `{"error": ...}` chunk while its llama.cpp
         backend is still warming up ("Couldn't connect to server"). The SDK
         raises this as a bare APIError mid-stream; we back off and retry, which
         absorbs the warm-up window — no manual readiness poll / unload-load
         needed, lemonade JIT-loads the model named in the request.

    Streaming is still deliberate (ADR 0005): a dropped client socket lets the
    single-slot local server abort generation instead of orphaning it. 429 is
    re-raised as LLMRateLimitError so call_llm's fallback path triggers.
    """
    from openai import APIError, APITimeoutError, RateLimitError

    # A real cloud call answers in seconds; cap its timeout well below the
    # caller's (long, local-prompt-eval) budget so a hung cloud fails fast →
    # fall back to local. Only when actually hitting cloud OpenRouter — a local
    # OpenAI-compatible server (lemonade eval judge) keeps the long budget.
    client_timeout = timeout
    if "openrouter.ai" in settings.openrouter_api_base:
        client_timeout = min(timeout, float(settings.cloud_call_timeout_s))

    client, create_kwargs = _build_openrouter_kwargs(model, system, user, schema, client_timeout)

    last_err: Exception | None = None
    for attempt in range(4):
        try:
            return _collect_openrouter_stream(client.chat.completions.create(**create_kwargs))
        except RateLimitError as e:
            # SDK already backed off across its own retries — hand straight to
            # call_llm's fallback instead of looping more.
            raise LLMRateLimitError(
                "OpenRouter rate limit (429) — free-tier quota exhausted, retry shortly."
            ) from e
        except APITimeoutError as e:
            # A hung cloud call — fail fast to the local fallback; don't burn the
            # 4× retry loop on something that already didn't answer in time.
            raise LLMRateLimitError(f"OpenRouter timeout after {client_timeout:.0f}s") from e
        except (APIError, RuntimeError) as e:
            # APIError: transport hiccup or streamed warm-up error chunk.
            # RuntimeError: thinking-budget exhaustion (see above) — retrying
            # is worth it because thinking length is stochastic.
            last_err = e
            time.sleep(2**attempt)
    raise RuntimeError(f"OpenRouter call failed after retries: {last_err}")


def _call_anthropic(model: str, system: str, user: str, json_only: bool) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    if json_only:
        # Anthropic has no schema mode, so we lean on a strict instruction.
        system = system + "\n\nReturn ONLY a single JSON object. No prose, no code fences."
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    block = resp.content[0]
    return getattr(block, "text", "")


class DefaultLLMClient:
    """Adapter exposing `call_llm` behind the LLMClient Protocol (ADR 0021).

    Delegates to the module-level `call_llm`, preserving the backend dispatch +
    fallback chain and letting source-level patches keep working. Consumers
    depend on the `LLMClient` Protocol and resolve the impl via `get_llm_client`,
    so a fake can be injected without monkeypatching the module function.
    """

    # jscpd:ignore-start — signature must mirror the LLMClient Protocol
    # (structural typing); the parallelism is mandated by the type system, not
    # copy-paste, so it is not a DRY violation to consolidate.
    def call_llm(
        self,
        system: str,
        user: str,
        *,
        model: str,
        schema: dict | None = None,
        timeout: float = 120.0,
        backend: str | None = None,
        meta: dict | None = None,
    ) -> str:
        # jscpd:ignore-end
        return call_llm(
            system,
            user,
            model=model,
            schema=schema,
            timeout=timeout,
            backend=backend,
            meta=meta,
        )


# Provider singleton lives in backend.providers (reset_all-able); the alias
# keeps the historical import path working.
from backend.providers import get_llm_client as get_llm_client  # noqa: E402
