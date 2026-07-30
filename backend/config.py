from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Paths
    data_dir: Path = Path("data")
    db_path: Path = Path("data/film_library.db")
    dimension_mapping_path: Path = Path("data/dimension-mapping.json")
    # Demo-chip queries warmed at startup (same file the frontend renders).
    # Compose mounts it read-only at /app/chips.json and points here via env.
    chips_path: Path = Path("frontend/chips.json")
    tmdb_cache_dir: Path = Path("data/tmdb_enriched")
    feedback_dir: Path = Path("data/feedback")

    # API Keys
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    tmdb_api_key: str = ""

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "film_vectors"

    # LLM — single model per backend (no consultant/escalation tier).
    llm_backend: str = "ollama"  # ollama | anthropic | gemini | openrouter
    primary_model: str = "qwen3:8b"
    anthropic_primary_model: str = "claude-sonnet-4-20250514"
    # gemini-3.5-flash is fast + free-tier friendly.
    gemini_primary_model: str = "gemini-3.5-flash"
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    # OpenRouter (OpenAI-compatible) cloud backend for tagging. `openrouter/free`
    # is an auto-router that picks among currently-live free models (filtering
    # for structured-output support) — it self-heals when a single free model is
    # pulled, which kept happening to us (glm-4.5-air:free was delisted, then the
    # whole GLM line dropped its free tier). Churn-proof beats pinning one slug.
    openrouter_api_key: str = ""
    # gpt-oss-20b:free over the openrouter/free auto-router: the router is
    # non-deterministic and a routed model returned Chinese labels as tag_ids
    # (unmatchable); gpt-oss-20b returns a clean array our orientation-agnostic
    # parser recovers. Small (20B) → fastest free option (~30s vs 80B timeouts).
    openrouter_primary_model: str = "openai/gpt-oss-20b:free"
    openrouter_api_base: str = "https://openrouter.ai/api/v1"
    # OpenRouter free-tier attribution headers (optional; harmless if blank).
    # Set OPENROUTER_REFERER to your own app/repo URL if you want attribution.
    openrouter_referer: str = ""
    openrouter_title: str = "AI Film Library"
    # Output cap for OpenAI-compatible calls. Qwen3 recommends 32768 typical /
    # 38912 hard with a 65536 API ceiling; the local Qwen3 judge can spend
    # tens of thousands of tokens on reasoning_content (lemonade can't disable
    # thinking, #1511) and emit empty content if it runs out before the
    # answer. We sit at the API ceiling so the hardest queries still have room
    # — bounded by the 256k context not VRAM, only costs time worst-case.
    openrouter_max_tokens: int = 65536
    # Whether to send response_format={"type":"json_object"} when the caller
    # asks for JSON. Set false to bypass the server's GBNF/json-schema grammar
    # path — local llama.cpp servers (Vulkan/MoE) have known stack-overflow
    # crashes there after hundreds of sustained constrained-decode calls
    # (llama.cpp #18988 / #19008 / #19010). Prompt-side "Return ONLY JSON"
    # instructions are still appended either way; this only governs the
    # server-side grammar enforcement.
    # Default OFF: `{"type":"json_object"}` forces a top-level OBJECT, but the
    # tagging prompt wants a JSON *array* — so the model emits one object and
    # stops (→ 0 tags parsed). With the flag off the model follows the prompt
    # ("return a JSON array") and we get the full list; the appended "Return
    # ONLY JSON" instruction + json.loads keep it well-formed.
    openrouter_use_response_format: bool = False
    # Tagging (auto-tag / re-analyze) prefers a CLOUD model when one is healthy:
    # the 8GB CPU box can't run the full-taxonomy prompt on qwen2.5:1.5b in
    # usable time (~150s, 1 weak tag). A circuit breaker (backend.llm_client)
    # skips the cloud the moment it 429s/errors — no per-request retry wait — and
    # half-opens after a cooldown to auto-recover. Empty string disables the
    # cloud preference (pure local). Query-expansion stays on llm_backend (local,
    # frequent, cheap) regardless. Cloud needs the matching API key in the env;
    # with no key the selector falls straight through to local.
    tagging_cloud_backend: str = "openrouter"
    tagging_cloud_cooldown_s: int = 300  # circuit stays open this long after a cloud failure
    # A healthy cloud call answers in seconds; if it hasn't in this long it's
    # broken, not slow. Cap the cloud HTTP timeout here (separate from the long
    # local prompt-eval budget) so a hung cloud fails fast → fall back to local.
    cloud_call_timeout_s: int = 50
    # Fallback when the primary backend fails (429 / connection / timeout). Local
    # Ollama is the safety net so cloud tagging degrades to local instead of a
    # hard error. For local-primary calls (query expansion) fb == primary, so the
    # chain is a no-op there.
    llm_fallback_backend: str = "ollama"
    llm_fallback_model: str = "qwen2.5:1.5b"

    # Embedding
    embedding_backend: str = "ollama"  # ollama | sentence-transformers
    embedding_model: str = "bge-m3"  # ollama tag name, or HF id for ST backend
    embedding_dim: int = 1024
    ollama_host: str = "http://localhost:11434"

    # Search query understanding (ADR 0002): one LLM call → taxonomy filters +
    # HyDE text + keywords. Cached + falls back to keyword parsing on failure.
    use_query_expansion: bool = True
    query_expansion_timeout: float = 20.0

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
