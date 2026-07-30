"""HTTP layer for /api/search — request/response mapping + DI only.

All business logic (query planning, hybrid recall, ranking, the heavy result
cache) lives in backend/services/search/; this router injects the Protocol
implementations (ADR 0021) and maps domain errors to HTTP codes.
"""

from fastapi import APIRouter, Depends, HTTPException

from backend.interfaces import Reranker, VectorStore
from backend.models import SearchRequest, SearchResponse
from backend.ratelimit import rate_limit_search
from backend.services.reranker import get_reranker
from backend.services.search import (
    FilmVectorNotFoundError,
    run_search,
)
from backend.services.search import (
    similar_films as similar_films_service,
)
from backend.vector_store import get_vector_store

router = APIRouter()


@router.post("/", response_model=SearchResponse, dependencies=[Depends(rate_limit_search)])
async def semantic_search(req: SearchRequest, reranker: Reranker = Depends(get_reranker)):
    """Hybrid search: vector + BM25 recall → RRF fusion → optional cross-encoder.

    See backend.services.search.service.run_search for the pipeline details.
    """
    return run_search(req, reranker)


@router.get(
    "/similar/{film_id}",
    response_model=SearchResponse,
    dependencies=[Depends(rate_limit_search)],
)
async def similar_films(
    film_id: str, top_k: int = 5, vector_store: VectorStore = Depends(get_vector_store)
):
    """Precomputed similar films, with a live raw-cosine fallback for films not
    yet precomputed. See backend.services.search.service.similar_films."""
    try:
        return similar_films_service(film_id, top_k, vector_store)
    except FilmVectorNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
