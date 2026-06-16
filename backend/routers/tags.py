from fastapi import APIRouter, Query

from backend.db import get_db, get_dimension_stats, get_films_by_tag, get_tags_by_dimension
from backend.models import DimensionStats, Tag, TagListResponse

router = APIRouter()


@router.get("/", response_model=TagListResponse)
def list_tags(dimension: str | None = Query(None)):
    with get_db() as conn:
        rows = get_tags_by_dimension(conn, dimension)
    tags = [Tag(**r) for r in rows]
    return TagListResponse(tags=tags, total=len(tags))


@router.get("/dimensions", response_model=list[DimensionStats])
def dimensions():
    with get_db() as conn:
        rows = get_dimension_stats(conn)
    return [DimensionStats(**r) for r in rows]


@router.get("/{tag_id}/films")
def films_by_tag(tag_id: str):
    with get_db() as conn:
        rows = get_films_by_tag(conn, tag_id)
    return {"tag_id": tag_id, "films": rows, "total": len(rows)}
