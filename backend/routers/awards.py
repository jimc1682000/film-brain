"""Award-tracker API: thin HTTP layer over award_view + award_manager."""

from fastapi import APIRouter, HTTPException, Query

from backend.award_manager import get_org, load_orgs, record_nomination
from backend.award_view import list_nominees_with_films, list_recent_batches
from backend.db import get_db
from backend.models import (
    AwardBatchSummary,
    AwardIngestMatch,
    AwardIngestRequest,
    AwardIngestResponse,
    AwardNominee,
    AwardOrg,
)

router = APIRouter(prefix="/api/awards", tags=["awards"])


@router.get("/orgs", response_model=list[AwardOrg])
def list_orgs() -> list[AwardOrg]:
    """Return all tracked award organisations."""
    return [AwardOrg(**o) for o in load_orgs().values()]


@router.post("/ingest", response_model=AwardIngestResponse)
def ingest_nominations(req: AwardIngestRequest) -> AwardIngestResponse:
    """Bulk insert one ceremony's nominations; matches to CATCHPLAY films."""
    try:
        org = get_org(req.org_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    matched: list[AwardIngestMatch] = []
    unmatched: list[AwardIngestMatch] = []

    with get_db() as conn:
        for nom in req.nominees:
            result = record_nomination(
                conn,
                org=org,
                year=req.year,
                category=nom.category,
                primary_title=nom.film_title_primary,
                alt_title=nom.film_title_alt,
                person=nom.person,
                result=nom.result.value,
                source_url=req.source_url,
                ceremony_date=req.ceremony_date,
            )
            m = AwardIngestMatch(
                category=result["category"],
                film_title=result["film_title"],
                person=result.get("person"),
                result=result["result"],
                tag_id=result["tag_id"],
                matched_film_id=result.get("matched_film_id"),
                matched_title=result.get("matched_title"),
                match_score=result.get("match_score"),
            )
            (matched if m.matched_film_id else unmatched).append(m)

    return AwardIngestResponse(
        org_id=req.org_id,
        year=req.year,
        total_nominees=len(req.nominees),
        matched=matched,
        unmatched=unmatched,
    )


@router.get("/recent-batches", response_model=list[AwardBatchSummary])
def recent_batches(limit: int = Query(default=20, ge=1, le=2000)) -> list[AwardBatchSummary]:
    """Latest ceremony batches with distinct-film coverage counts."""
    with get_db() as conn:
        return list_recent_batches(conn, limit=limit)


@router.get("/nominees", response_model=list[AwardNominee])
def list_nominees(
    tag_id: str | None = None,
    org_id: str | None = None,
    year: int | None = None,
    film_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[AwardNominee]:
    """Nominees (matched + unmatched) with TMDB + library metadata stitched."""
    if org_id:
        try:
            get_org(org_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    with get_db() as conn:
        return list_nominees_with_films(
            conn, tag_id=tag_id, org_id=org_id, year=year, film_id=film_id, limit=limit
        )
