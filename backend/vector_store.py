"""Qdrant vector store management for film embeddings."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, cast

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from backend.config import settings

if TYPE_CHECKING:
    from backend.interfaces import VectorStore


def get_qdrant_client() -> QdrantClient:
    """Connect to Qdrant Docker instance."""
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


def ensure_collection(client: QdrantClient | None = None) -> None:
    """Create film_vectors collection if it doesn't exist."""
    client = client or get_qdrant_client()
    collections = [c.name for c in client.get_collections().collections]
    if settings.qdrant_collection not in collections:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(
                size=settings.embedding_dim,
                distance=Distance.COSINE,
            ),
        )


def _point_id_for(film_id: str) -> int:
    """Deterministic Qdrant point ID from film_id. Python's hash() is salted
    per-process so it CANNOT be used here — md5 is stable across runs."""
    digest = hashlib.md5(film_id.encode("utf-8"), usedforsecurity=False).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def upsert_film_vector(
    client: QdrantClient,
    film_id: str,
    vector: list[float],
    payload: dict,
) -> None:
    """Upsert a single film vector into Qdrant."""
    point_id = _point_id_for(film_id)
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )


def delete_film_vector(client: QdrantClient, film_id: str) -> bool:
    """Remove the film's point from Qdrant. Returns True on best-effort success."""
    point_id = _point_id_for(film_id)
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=[point_id],
    )
    return True


def search_films(
    client: QdrantClient,
    query_vector: list[float],
    top_k: int = 10,
    dimension_filters: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Semantic search over film vectors with optional dimension filtering."""
    query_filter = None
    if dimension_filters:
        must_conditions = [
            FieldCondition(key=f"dim_{dim}", match=MatchValue(value=val))
            for dim, values in dimension_filters.items()
            for val in values
        ]
        if must_conditions:
            # qdrant-client's Filter.must is an invariant list of a big Condition
            # union; a list[FieldCondition] is a valid member but mypy rejects the
            # invariance. Safe in practice.
            query_filter = Filter(must=must_conditions)  # type: ignore[arg-type]

    results = client.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )

    out = []
    for hit in results.points:
        payload = hit.payload or {}
        out.append(
            {
                "film_id": payload.get("film_id", ""),
                "title_zh": payload.get("title_zh", ""),
                "title_en": payload.get("title_en"),
                "poster_url": payload.get("poster_url"),
                "score": hit.score,
                "tags": payload.get("tags", []),
            }
        )
    return out


def get_film_vector(client: QdrantClient, film_id: str) -> list[float] | None:
    """Retrieve a film's vector for similarity search."""
    point_id = _point_id_for(film_id)
    try:
        results = client.retrieve(
            collection_name=settings.qdrant_collection,
            ids=[point_id],
            with_vectors=True,
        )
        if results:
            vec = results[0].vector
            # Qdrant may return list[float] (single vector) or list[list[float]]
            # (multi-vector); we only support single.
            if isinstance(vec, list) and vec and not isinstance(vec[0], list):
                return [float(x) for x in vec]  # type: ignore[arg-type]
    except Exception:
        pass
    return None


def build_film_payload(film: dict, tags: list[dict]) -> dict:
    """Build Qdrant payload from film + tags data."""
    payload = {
        "film_id": film["film_id"],
        "title_zh": film.get("title_zh", ""),
        "title_en": film.get("title_en"),
        "poster_url": film.get("poster_url"),
        "tags": [t["tag_id"] for t in tags],
    }

    # Per-dimension tag arrays for filtering
    dim_tags: dict[str, list[str]] = {}
    for t in tags:
        dim = t.get("dimension", "unknown")
        dim_tags.setdefault(dim, []).append(t["tag_id"])

    for dim, tag_ids in dim_tags.items():
        payload[f"dim_{dim}"] = tag_ids

    return payload


class QdrantVectorStore:
    """Adapter exposing semantic search behind the VectorStore Protocol (ADR 0021).

    Delegates to the module-level `search_films`, so source-level patches keep
    working unchanged. The client-passing signature is a known leaky abstraction
    (the store should arguably own its client) — left as-is to avoid pulling the
    indexing/upsert paths into this increment; see ADR 0021's follow-up note.
    """

    def search_films(
        self,
        client: object,
        query_vector: list[float],
        top_k: int = 10,
        dimension_filters: dict[str, list[str]] | None = None,
    ) -> list[dict]:
        # `client: object` matches the VectorStore Protocol (which stays
        # DB-agnostic); DI always wires in a real QdrantClient, so cast for the
        # concrete module call. The client param itself is the leaky abstraction
        # noted in ADR 0021 — to be removed when the store owns its client.
        return search_films(
            cast(QdrantClient, client),
            query_vector,
            top_k=top_k,
            dimension_filters=dimension_filters,
        )


_vector_store: QdrantVectorStore | None = None


def get_vector_store() -> VectorStore:
    """Return the process-wide VectorStore (ADR 0021 injection seam).

    Consumers depend on the `VectorStore` Protocol and resolve the concrete impl
    through this provider, so a fake can be injected (param / FastAPI override)
    without monkeypatching the module function.
    """
    global _vector_store
    if _vector_store is None:
        _vector_store = QdrantVectorStore()
    return _vector_store
