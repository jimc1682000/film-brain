"""Boundary contracts (Protocols) for the system's external dependencies.

These define the *seams* between our code and the heavy/external services —
the embedding model, the vector store, the cross-encoder reranker, and the LLM.
Each boundary has an adapter implementing its Protocol, resolved through a
provider (`get_embed_service` / `get_vector_store` / `get_reranker` /
`get_llm_client`); consumers depend on the Protocol and the concrete impl is
injected (default param / FastAPI `Depends`), so tests pass an explicit fake
rather than monkeypatching a module name (ADR 0021, step 2 complete).

Protocols are structural + runtime-checkable: an object that has matching
methods satisfies the contract with no inheritance and no import cycle.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Turns text into vectors. Impl: services.embedder.EmbedService."""

    # Per-tag vectors cached after warmup; the search path reads it to score
    # tag relevance without re-embedding (see routers.search._rerank_tags).
    tag_vector_cache: dict[str, list[float]]

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_single(self, text: str) -> list[float]: ...

    def warmup_tag_cache(self, registry: object) -> int: ...


@runtime_checkable
class VectorStore(Protocol):
    """Semantic vector search over films. Impl: vector_store.QdrantVectorStore."""

    def search_films(
        self,
        client: object,
        query_vector: list[float],
        top_k: int = 10,
        dimension_filters: dict[str, list[str]] | None = None,
    ) -> list[dict]: ...


@runtime_checkable
class Reranker(Protocol):
    """Cross-encoder precision rerank. Impl: reranker.CrossEncoderReranker.

    Returns the reordered candidates, or None when reranking is skipped/unavailable
    (the caller then keeps the retrieval order).
    """

    def rerank(self, query: str, candidates: list[dict]) -> list[dict] | None: ...


@runtime_checkable
class LLMClient(Protocol):
    """Single-completion LLM call. Impl: llm_client.DefaultLLMClient."""

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
    ) -> str: ...
