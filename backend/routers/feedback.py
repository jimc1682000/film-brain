"""Feedback wiki — thin slice: list, get, reanalyze."""

from fastapi import APIRouter, HTTPException

from backend.feedback_store import get_page, list_pages
from backend.models import FeedbackPage, ReanalyzeRequest
from backend.services import get_feedback_service

router = APIRouter()


@router.get("/pages", response_model=list[FeedbackPage])
def api_list_pages(status: str | None = None, kind: str | None = None):
    pages = list_pages()
    if status:
        pages = [p for p in pages if p.status == status]
    if kind:
        pages = [p for p in pages if p.kind == kind]
    return pages


@router.get("/pages/{page_id:path}", response_model=FeedbackPage)
def api_get_page(page_id: str):
    page = get_page(page_id)
    if page is None:
        raise HTTPException(status_code=404, detail=f"page not found: {page_id}")
    return page


@router.post("/pages/{page_id:path}/reanalyze")
async def api_reanalyze(page_id: str, req: ReanalyzeRequest):
    if get_page(page_id) is None:
        raise HTTPException(status_code=404, detail=f"page not found: {page_id}")
    service = get_feedback_service()
    try:
        return await service.execute(
            {
                "op": "reanalyze",
                "page_id": page_id,
                "prompt": req.prompt,
                "use_consultant": req.use_consultant,
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
