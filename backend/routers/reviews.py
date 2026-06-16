"""Human-in-the-loop tag review — approve / reject / modify AI suggestions."""

from fastapi import APIRouter, HTTPException

from backend.db import (
    delete_film_tag,
    get_db,
    get_film,
    get_reviews_for_film,
    get_tag_reject_stats,
    insert_film_tag,
    insert_tag_review,
    update_film_tag_source,
)
from backend.models import (
    ReviewAction,
    ReviewRecord,
    ReviewRequest,
    ReviewResponse,
    TagRejectStat,
)
from backend.tag_registry import TagRegistry

router = APIRouter()

_registry: TagRegistry | None = None


def _get_registry() -> TagRegistry:
    global _registry
    if _registry is None:
        _registry = TagRegistry()
    return _registry


@router.post("/films/{film_id}/reviews", response_model=ReviewResponse)
async def submit_review(film_id: str, req: ReviewRequest):
    """Record a human review action on a film's tag.

    - approved: mark film_tag source='human-approved'
    - rejected: delete film_tag row (tag_reviews record retained)
    - modified: delete old tag + insert replacement_tag_id with manual source
    """
    with get_db() as conn:
        film = get_film(conn, film_id)
        if not film:
            raise HTTPException(status_code=404, detail="Film not found")

    registry = _get_registry()
    valid_ids = registry.all_tag_ids
    if req.tag_id not in valid_ids:
        raise HTTPException(status_code=400, detail=f"unknown tag_id: {req.tag_id}")

    action = req.action.value
    replacement_tag_id: str | None = None

    with get_db() as conn:
        if req.action == ReviewAction.approved:
            update_film_tag_source(conn, film_id, req.tag_id, "human-approved")
            insert_tag_review(conn, film_id, req.tag_id, action, req.reviewer)

        elif req.action == ReviewAction.rejected:
            delete_film_tag(conn, film_id, req.tag_id)
            insert_tag_review(conn, film_id, req.tag_id, action, req.reviewer)

        elif req.action == ReviewAction.modified:
            if not req.replacement_tag_id:
                raise HTTPException(
                    status_code=400, detail="modified action requires replacement_tag_id"
                )
            if req.replacement_tag_id not in valid_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown replacement_tag_id: {req.replacement_tag_id}",
                )
            delete_film_tag(conn, film_id, req.tag_id)
            insert_film_tag(
                conn,
                film_id,
                req.replacement_tag_id,
                confidence=req.replacement_confidence or 1.0,
                source="manual",
            )
            insert_tag_review(conn, film_id, req.tag_id, action, req.reviewer)
            insert_tag_review(conn, film_id, req.replacement_tag_id, "approved", req.reviewer)
            replacement_tag_id = req.replacement_tag_id

    return ReviewResponse(
        film_id=film_id,
        tag_id=req.tag_id,
        action=action,
        replacement_tag_id=replacement_tag_id,
        success=True,
    )


@router.get("/films/{film_id}/reviews", response_model=list[ReviewRecord])
async def list_reviews(film_id: str):
    """Return all review actions recorded against a film (most recent first)."""
    with get_db() as conn:
        film = get_film(conn, film_id)
        if not film:
            raise HTTPException(status_code=404, detail="Film not found")
        return get_reviews_for_film(conn, film_id)


@router.get("/reviews/stats", response_model=list[TagRejectStat])
async def review_stats(min_reviews: int = 3):
    """Per-tag reject rate across the library (tags with ≥ min_reviews only)."""
    with get_db() as conn:
        return get_tag_reject_stats(conn, min_reviews=min_reviews)
