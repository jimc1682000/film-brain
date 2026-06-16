from fastapi import APIRouter, HTTPException, Query

from backend.db import (
    delete_film,
    get_db,
    get_film,
    get_film_tags,
    get_recent_tag_activity_films,
)
from backend.models import Film, FilmDetail, FilmListResponse, FilmTag
from backend.vector_store import delete_film_vector, get_qdrant_client

router = APIRouter()


@router.get("/", response_model=FilmListResponse)
def list_films(search: str | None = Query(None), limit: int = 50, offset: int = 0):
    with get_db() as conn:
        if search:
            rows = conn.execute(
                "SELECT * FROM films WHERE title_zh LIKE ? OR title_en LIKE ? "
                "ORDER BY title_zh LIMIT ? OFFSET ?",
                (f"%{search}%", f"%{search}%", limit, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM films WHERE title_zh LIKE ? OR title_en LIKE ?",
                (f"%{search}%", f"%{search}%"),
            ).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT * FROM films ORDER BY title_zh LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM films").fetchone()[0]
    films = [Film(**dict(r)) for r in rows]
    return FilmListResponse(films=films, total=total)


@router.get("/recent-tag-activity")
def recent_tag_activity(limit: int = 10):
    """Top N films by most recent tag add/update.

    Route placed before the dynamic /{film_id} route so FastAPI does not treat
    'recent-tag-activity' as a film_id.
    """
    with get_db() as conn:
        rows = get_recent_tag_activity_films(conn, limit=limit)
    return {"films": rows, "total": len(rows)}


@router.get("/{film_id}", response_model=FilmDetail)
def get_film_detail(film_id: str):
    with get_db() as conn:
        film = get_film(conn, film_id)
        if not film:
            raise HTTPException(status_code=404, detail="Film not found")
        tag_rows = get_film_tags(conn, film_id)
    tags = [FilmTag(**r) for r in tag_rows]
    return FilmDetail(**film, tags=tags)


@router.delete("/{film_id}")
def delete_film_endpoint(film_id: str):
    """Cascade-delete a film and clean up dependent rows.

    Drops film_tags + tag_reviews for this film, unlinks award_nominees
    that pointed at it (the nominee row itself stays — it's still a real
    nomination, just no longer matched to our library), and removes the
    Qdrant vector so semantic search stops surfacing the film. Returns
    deletion counts so the UI can show a confirmation summary.
    """
    with get_db() as conn:
        film = get_film(conn, film_id)
        if not film:
            raise HTTPException(status_code=404, detail="Film not found")
        counts = delete_film(conn, film_id)

    # Vector removal is best-effort; a missing/unreachable Qdrant should
    # not block the SQL-side delete (which is the source of truth).
    try:
        delete_film_vector(get_qdrant_client(), film_id)
        counts["vector_deleted"] = True
    except Exception:
        counts["vector_deleted"] = False

    return {"film_id": film_id, **counts}
