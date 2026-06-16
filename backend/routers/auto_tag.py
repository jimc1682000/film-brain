import re
import uuid

from fastapi import APIRouter, HTTPException

from backend.config import settings
from backend.db import (
    delete_film_tags_by_source,
    get_db,
    get_film,
    get_film_tags,
    insert_film,
    insert_film_tag,
)
from backend.llm_client import LLMRateLimitError
from backend.models import (
    AutoTagAcceptRequest,
    AutoTagPreviewRequest,
    AutoTagResponse,
    CreateFilmRequest,
    CreateFilmResponse,
    SaveTagsRequest,
    SaveTagsResponse,
    TaggingContextResponse,
)
from backend.services import get_auto_tag_service
from backend.services.auto_tag import OUTPUT_SCHEMA
from backend.tag_registry import TagRegistry
from backend.tmdb_lookup import catchplay_poster

router = APIRouter()

_registry: TagRegistry | None = None

_CATCHPLAY_UUID = re.compile(
    r"/video/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _get_registry() -> TagRegistry:
    global _registry
    if _registry is None:
        _registry = TagRegistry()
    return _registry


def _parse_catchplay_uuid(url: str | None) -> str | None:
    m = _CATCHPLAY_UUID.search(url or "")
    return m.group(1).lower() if m else None


def _embed_film(film: dict, tag_rows: list[dict]) -> bool:
    """Best-effort: embed + upsert to Qdrant so the new film is searchable.

    Never blocks the insert — if Qdrant is down or the embed model is
    unavailable, the SQL rows still land and embedding can be regenerated.
    """
    try:
        from backend.services import get_embed_service
        from backend.services.embedder import EmbedService
        from backend.vector_store import build_film_payload, get_qdrant_client, upsert_film_vector

        film = {**film, "tag_labels": [f"{t['label_zh_tw']}({t['label_en']})" for t in tag_rows]}
        vector = get_embed_service().embed_single(EmbedService.build_film_text(film))
        upsert_film_vector(
            get_qdrant_client(), film["film_id"], vector, build_film_payload(film, tag_rows)
        )
        return True
    except Exception:
        return False


SYSTEM_PROMPT_FOR_CLAUDE_CODE = """You are a film classification expert for CATCHPLAY+ streaming platform.
Given a film's metadata, suggest 5-15 relevant tags from the provided taxonomy.

Rules:
1. ONLY use tag_ids from the provided taxonomy
2. Each tag must include a confidence score (0.0-1.0)
3. Provide brief reasoning (1 sentence) for each tag
4. Span multiple dimensions when relevant
5. Prefer the most distinctive tags for content discovery

Return JSON array of objects: {tag_id, dimension, confidence, reasoning}"""


# OUTPUT_SCHEMA is owned by the domain layer (services.auto_tag); the /context
# endpoint re-exposes it via the top-level import. Single source = no drift.


@router.get("/{film_id}/context", response_model=TaggingContextResponse)
async def tagging_context(film_id: str):
    """Return film + taxonomy context for Claude Code to perform tagging locally."""
    with get_db() as conn:
        film = get_film(conn, film_id)
        if not film:
            raise HTTPException(status_code=404, detail="Film not found")

    return TaggingContextResponse(
        film=film,
        taxonomy_context=_get_registry().to_prompt_context(),
        system_prompt=SYSTEM_PROMPT_FOR_CLAUDE_CODE,
        output_schema=OUTPUT_SCHEMA,
    )


@router.post("/{film_id}/save", response_model=SaveTagsResponse)
async def save_tags(film_id: str, req: SaveTagsRequest):
    """Persist tag suggestions. Validates tag_ids against registry; skips invalid."""
    with get_db() as conn:
        film = get_film(conn, film_id)
        if not film:
            raise HTTPException(status_code=404, detail="Film not found")

    registry = _get_registry()
    valid_ids = registry.all_tag_ids

    saved: list[str] = []
    invalid: list[str] = []
    with get_db() as conn:
        # Clear prior suggestions from this same source so re-analyze replaces rather
        # than accumulates (avoids ghost tags users can't see in the UI).
        if req.source.startswith("ai"):
            delete_film_tags_by_source(conn, film_id, req.source)
        for s in req.suggestions:
            if s.tag_id not in valid_ids:
                invalid.append(s.tag_id)
                continue
            insert_film_tag(conn, film_id, s.tag_id, confidence=s.confidence, source=req.source)
            saved.append(s.tag_id)

    return SaveTagsResponse(film_id=film_id, saved=saved, invalid=invalid, total_saved=len(saved))


@router.post("/preview", response_model=AutoTagResponse)
async def auto_tag_preview(req: AutoTagPreviewRequest):
    """Ad-hoc auto-tag on a raw film dict without DB persistence.

    Use case: editor wants to try tagging a film that's not yet in the DB (新片).
    Flow: optional TMDB enrich → LLM tag. No film_tags rows inserted.
    Declared before /{film_id} so FastAPI matches the literal path first.
    """
    film = {
        "film_id": "preview",
        "title_zh": req.title_zh,
        "title_en": req.title_en,
        "description": req.description,
        "original_genre": req.original_genre,
        "tmdb_overview": req.tmdb_overview,
        "tmdb_genres": req.tmdb_genres,
        "tmdb_keywords": req.tmdb_keywords,
        "tmdb_cast": req.tmdb_cast,
        "tmdb_director": req.tmdb_director,
    }

    # Optional TMDB enrichment. User-supplied fields win over TMDB to respect
    # explicit overrides. Silent fallthrough on missing key / lookup failure.
    applied_enrichment: dict | None = None
    if req.enrich and settings.tmdb_api_key:
        from backend.services.enrichment import EnrichService

        try:
            enriched = await EnrichService().execute(
                {"title_zh": req.title_zh, "title_en": req.title_en or ""}
            )
            if "error" not in enriched:
                applied = {}
                for k, v in enriched.items():
                    if film.get(k) in (None, "") and v not in (None, ""):
                        film[k] = v
                        applied[k] = v
                if applied:
                    applied_enrichment = applied
        except Exception:
            pass

    service = get_auto_tag_service()
    try:
        result = await service.execute({"film": film, "locale": req.locale})
    except LLMRateLimitError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if applied_enrichment:
        result["enriched_film"] = applied_enrichment
    return result


@router.post("/create", response_model=CreateFilmResponse)
async def create_film_from_preview(req: CreateFilmRequest):
    """Persist a previewed new film + accepted tags into the library.

    Mirrors the import pipeline's end state: films row + film_tags (source="ai")
    + a best-effort Qdrant embedding so the film is searchable. film_id is the
    CATCHPLAY+ video UUID when a url is given (matching the catalogue +
    deduping), otherwise a generated uuid4.
    Declared before /{film_id} so FastAPI matches the literal path first.
    """
    film_id = _parse_catchplay_uuid(req.catchplay_url) or str(uuid.uuid4())

    # Poster priority: manual override > CATCHPLAY+ og:image > TMDB poster.
    # CP og:image is the nicest art but geo-gated to TW (the EU VPS gets a
    # generic logo, rejected inside catchplay_poster), so the geo-immune TMDB
    # poster from enrich is the practical fallback for films created on the VPS.
    poster_url = req.poster_url or catchplay_poster(req.catchplay_url) or req.tmdb_poster_url

    registry = _get_registry()
    valid_ids = registry.all_tag_ids

    with get_db() as conn:
        if get_film(conn, film_id):
            raise HTTPException(status_code=409, detail=f"影片已存在: {film_id}")
        insert_film(
            conn,
            film_id=film_id,
            title_zh=req.title_zh,
            title_en=req.title_en,
            description=req.description,
            catchplay_url=req.catchplay_url,
            poster_url=poster_url,
            original_genre=req.original_genre,
            tmdb_id=req.tmdb_id,
            tmdb_overview=req.tmdb_overview,
            tmdb_genres=req.tmdb_genres,
            tmdb_keywords=req.tmdb_keywords,
            tmdb_vote_avg=req.tmdb_vote_avg,
            tmdb_cast=req.tmdb_cast,
            tmdb_director=req.tmdb_director,
        )
        saved = 0
        for s in req.tags:
            if s.tag_id not in valid_ids:
                continue
            insert_film_tag(conn, film_id, s.tag_id, confidence=s.confidence, source="ai")
            saved += 1
        film_row = get_film(conn, film_id)
        tag_rows = get_film_tags(conn, film_id)

    # film_row was just inserted above, so it always exists here.
    assert film_row is not None
    embedded = _embed_film(film_row, tag_rows)
    return CreateFilmResponse(film_id=film_id, saved_tags=saved, embedded=embedded)


@router.post("/{film_id}", response_model=AutoTagResponse)
async def auto_tag_film(film_id: str, locale: str = "zh_TW"):
    """Backend LLM auto-tagging (single model pass).

    Re-analyze just calls this again — a fresh pass with the same model.
    locale sets the reasoning output language (zh_TW, zh_CN, en, ...).
    """
    with get_db() as conn:
        film = get_film(conn, film_id)
        if not film:
            raise HTTPException(status_code=404, detail="Film not found")

    service = get_auto_tag_service()
    try:
        result = await service.execute({"film": film, "locale": locale})
    except LLMRateLimitError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return result


@router.post("/{film_id}/accept")
async def accept_tags(film_id: str, req: AutoTagAcceptRequest):
    """Legacy accept endpoint (used by backend LLM flow). Prefer /save."""
    with get_db() as conn:
        film = get_film(conn, film_id)
        if not film:
            raise HTTPException(status_code=404, detail="Film not found")

    if not req.tag_ids:
        return {"status": "no tags specified"}

    with get_db() as conn:
        accepted = 0
        for tag_id in req.tag_ids:
            insert_film_tag(conn, film_id, tag_id, confidence=1.0, source="ai-accepted")
            accepted += 1

    return {"status": "accepted", "film_id": film_id, "accepted_count": accepted}
