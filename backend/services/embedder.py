"""EmbedService — film embedding generator.

Supports two backends:
- ollama (default): local Ollama daemon, model pulled via `ollama pull bge-m3`
- sentence-transformers: direct HF download into local model cache
"""

import json

from backend.config import settings
from backend.services.base import BaseService


class EmbedService(BaseService):
    """Generate embeddings for film text."""

    def __init__(self):
        self._ollama = None
        self._st_model = None
        # tag_id -> normalized embedding. Populated by warmup_tag_cache at
        # startup so search.py can rerank film.tags against query vector without
        # hitting the embedder per-request.
        self.tag_vector_cache: dict[str, list[float]] = {}

    @property
    def name(self) -> str:
        return "embedder"

    def warmup_tag_cache(self, registry) -> int:
        """Pre-embed all active tag labels for search-time reranking.

        Returns count of cached vectors. Safe to call multiple times (re-embeds).
        """
        tag_ids: list[str] = []
        texts: list[str] = []
        for tid in sorted(registry.all_tag_ids):
            tag = registry.get_tag(tid)
            if not tag:
                continue
            labels = tag.get("labels", {})
            zh = labels.get("zh_TW", "")
            en = labels.get("en", tid)
            # Combine zh + en so cosine captures both languages' semantics
            text = f"{zh} {en}".strip() or tid
            tag_ids.append(tid)
            texts.append(text)
        if not texts:
            return 0
        vectors = self.embed(texts)
        self.tag_vector_cache = dict(zip(tag_ids, vectors, strict=False))
        return len(self.tag_vector_cache)

    def _embed_ollama(self, texts: list[str]) -> list[list[float]]:
        if self._ollama is None:
            import ollama

            self._ollama = ollama.Client(host=settings.ollama_host)
        resp = self._ollama.embed(model=settings.embedding_model, input=texts)
        return resp["embeddings"]

    def _embed_st(self, texts: list[str]) -> list[list[float]]:
        if self._st_model is None:
            # Optional dep (requirements-st.txt) — absent from the slim image.
            import sentence_transformers as st  # pyright: ignore[reportMissingImports]

            self._st_model = st.SentenceTransformer(settings.embedding_model)
        return self._st_model.encode(texts, normalize_embeddings=True).tolist()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if settings.embedding_backend == "ollama":
            return self._embed_ollama(texts)
        if settings.embedding_backend == "sentence-transformers":
            return self._embed_st(texts)
        raise ValueError(f"unknown embedding_backend: {settings.embedding_backend}")

    def embed_single(self, text: str) -> list[float]:
        return self.embed([text])[0]

    async def execute(self, input_data: dict) -> dict:
        texts = input_data.get("texts", [])
        embeddings = self.embed(texts)
        return {"embeddings": embeddings, "dim": settings.embedding_dim}

    @staticmethod
    def build_film_text(film: dict) -> str:
        """Build composite text for film embedding."""
        parts = []
        if film.get("title_zh"):
            parts.append(film["title_zh"])
        if film.get("title_en"):
            parts.append(film["title_en"])
        if film.get("description"):
            parts.append(film["description"])
        if film.get("tmdb_overview"):
            parts.append(film["tmdb_overview"])

        if film.get("tag_labels"):
            parts.append("Tags: " + ", ".join(film["tag_labels"]))

        for field in ("tmdb_genres", "tmdb_keywords"):
            raw = film.get(field)
            if raw:
                try:
                    items = json.loads(raw) if isinstance(raw, str) else raw
                    parts.append(", ".join(items))
                except (json.JSONDecodeError, TypeError):
                    pass

        return " | ".join(parts)
