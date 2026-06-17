from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# --- Tag models ---


class Tag(BaseModel):
    tag_id: str
    dimension: str
    label_en: str
    label_zh_tw: str
    label_in_id: str | None = None
    source: str = "migrated"
    status: str = "active"


class DimensionStats(BaseModel):
    dimension: str
    tag_count: int
    used_tag_count: int = 0


# --- Film models ---


class Film(BaseModel):
    film_id: str
    title_zh: str
    title_en: str | None = None
    description: str | None = None
    catchplay_url: str | None = None
    poster_url: str | None = None
    original_genre: str | None = None
    tmdb_id: int | None = None
    tmdb_vote_avg: float | None = None


class FilmDetail(Film):
    description_raw: str | None = None
    tmdb_overview: str | None = None
    tmdb_genres: str | None = None
    tmdb_keywords: str | None = None
    tmdb_cast: str | None = None
    tmdb_director: str | None = None
    tmdb_backdrop_url: str | None = None  # wide still for the detail hero
    tags: list["FilmTag"] = []


class FilmTag(BaseModel):
    tag_id: str
    dimension: str
    label_en: str
    label_zh_tw: str
    confidence: float
    source: str
    award_year: int | None = None
    award_result: str | None = None


# --- Auto-tag models ---


class TagSuggestion(BaseModel):
    tag_id: str
    dimension: str
    label_zh_tw: str = ""
    label_en: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class AutoTagResponse(BaseModel):
    film_id: str
    title: str
    suggestions: list[TagSuggestion]
    model_used: str
    escalated: bool = False
    timestamp: datetime = Field(default_factory=datetime.now)
    # Populated by /preview when enrich=True — lets UI show what TMDB filled in.
    enriched_film: dict | None = None
    # Set when the cloud LLM was rate-limited and a local model served the
    # request — UI shows a warning banner.
    warning: str | None = None


class AutoTagAcceptRequest(BaseModel):
    tag_ids: list[str] | None = None  # None = accept all


class AutoTagPreviewRequest(BaseModel):
    """Ad-hoc auto-tag on a raw film dict without DB persistence."""

    title_zh: str
    title_en: str | None = None
    description: str | None = None
    original_genre: str | None = None
    tmdb_overview: str | None = None
    tmdb_genres: str | None = None
    tmdb_keywords: str | None = None
    tmdb_cast: str | None = None
    tmdb_director: str | None = None
    locale: str = "zh_TW"
    enrich: bool = True  # if True, fetch TMDB fields before tagging


class TaggingContextResponse(BaseModel):
    """Context for Claude Code to perform tagging locally — no backend LLM call."""

    film: dict
    taxonomy_context: str
    system_prompt: str
    output_schema: dict


class SaveTagSuggestion(BaseModel):
    tag_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class SaveTagsRequest(BaseModel):
    suggestions: list[SaveTagSuggestion]
    source: str = "ai"  # ai | ai-claude-code | manual


class SaveTagsResponse(BaseModel):
    film_id: str
    saved: list[str]
    invalid: list[str]
    total_saved: int


class CreateFilmRequest(BaseModel):
    """Persist a previewed new film + its accepted tags into the library.

    If catchplay_url is given, film_id is derived from its video UUID (so it
    matches the catalogue + dedupes). Otherwise a uuid4 is generated.
    """

    catchplay_url: str | None = None
    title_zh: str
    title_en: str | None = None
    description: str | None = None
    original_genre: str | None = None
    poster_url: str | None = None  # manual override (highest priority)
    tmdb_poster_url: str | None = None  # geo-immune fallback from enrich
    tmdb_id: int | None = None
    tmdb_overview: str | None = None
    tmdb_genres: str | None = None
    tmdb_keywords: str | None = None
    tmdb_vote_avg: float | None = None
    tmdb_cast: str | None = None
    tmdb_director: str | None = None
    tags: list[SaveTagSuggestion] = []


class CreateFilmResponse(BaseModel):
    film_id: str
    saved_tags: int
    embedded: bool


# --- Search models ---


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    dimension_filters: dict[str, list[str]] | None = None
    min_confidence: float = 0.6
    # Editor-visible floor: drop hits whose final displayed score falls below
    # this. Distinct from min_confidence (vector cosine pre-rerank). An editor
    # specifically asked to hide any result reporting <10% match.
    min_display_score: float = 0.1
    use_llm_rerank: bool = True
    rerank_pool: int = 20
    # Gate phase: AI 先聯想，只回傳 understanding 讓使用者自評方向，先不 search
    # 片子。確認後再以同一 query 走完整搜尋。正向修正折進 query 重新聯想。
    understand_only: bool = False
    # 使用者明確排除的方向（gate 點 ✕ 移除的 tag/keyword label）。NOT folded
    # into query — 結構化帶進來，後端反查 tag_id 後給負 boost、並從 keywords/
    # bm25 移除。query 因此保持純正向，embed/BM25 不被否定詞污染。
    exclude: list[str] = []


class SearchResult(BaseModel):
    film_id: str
    title_zh: str
    title_en: str | None = None
    poster_url: str | None = None
    score: float
    matched_tags: list[str] = []
    description_snippet: str = ""
    # Why this result — sources (vector/bm25) + matched preferential tag labels.
    explain: dict | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    total: int
    # How the system read the query — hard filters + keywords (banner).
    understanding: dict | None = None


# --- API list responses ---


class FilmListResponse(BaseModel):
    films: list[Film]
    total: int


class TagListResponse(BaseModel):
    tags: list[Tag]
    total: int


# --- Review models ---


class ReviewAction(StrEnum):
    approved = "approved"
    rejected = "rejected"
    modified = "modified"


class ReviewRequest(BaseModel):
    tag_id: str
    action: ReviewAction
    reviewer: str = "editor"
    replacement_tag_id: str | None = None
    replacement_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ReviewResponse(BaseModel):
    film_id: str
    tag_id: str
    action: str
    replacement_tag_id: str | None = None
    success: bool = True


class ReviewRecord(BaseModel):
    id: int
    film_id: str
    tag_id: str
    action: str
    reviewer: str | None = None
    created_at: datetime


class TagRejectStat(BaseModel):
    tag_id: str
    dimension: str | None = None
    label_zh_tw: str | None = None
    total_reviews: int
    rejected: int
    reject_rate: float


# --- Award-tracker models ---


class AwardResult(StrEnum):
    nominated = "nominated"
    won = "won"


class AwardNomineeInput(BaseModel):
    """One nominee / winner extracted from an official awards page."""

    category: str
    film_title_primary: str
    film_title_alt: str | None = None
    person: str | None = None
    result: AwardResult = AwardResult.nominated


class AwardIngestRequest(BaseModel):
    """Bulk payload from award-tracker skill after parsing an official page."""

    org_id: str
    year: int
    source_url: str
    ceremony_date: str | None = None
    nominees: list[AwardNomineeInput]


class AwardIngestMatch(BaseModel):
    category: str
    film_title: str
    person: str | None = None
    result: str
    tag_id: str
    matched_film_id: str | None = None
    matched_title: str | None = None
    match_score: float | None = None


class AwardIngestResponse(BaseModel):
    org_id: str
    year: int
    total_nominees: int
    matched: list[AwardIngestMatch]
    unmatched: list[AwardIngestMatch]


class AwardBatchSummary(BaseModel):
    tag_id: str
    tag_label_zh_tw: str
    year: int
    org_id: str
    total_count: int
    matched_count: int
    # Distinct-film counts at the (org_id, year) ceremony level. Same value
    # repeated for every category row of a ceremony so the frontend can
    # render the header without summing (which over-counts when a film
    # sweeps multiple categories).
    ceremony_nominated_films_total: int = 0
    ceremony_won_films_total: int = 0
    ceremony_nominated_films_matched: int = 0
    ceremony_won_films_matched: int = 0
    latest_insert: datetime


class AwardNominee(BaseModel):
    id: int
    org_id: str
    tag_id: str
    year: int
    category: str
    film_title_primary: str
    film_title_alt: str | None = None
    person: str | None = None
    result: str
    tmdb_id: int | None = None
    tmdb_media_type: str | None = None
    tmdb_title: str | None = None
    tmdb_year: int | None = None
    tmdb_poster_url: str | None = None
    tmdb_overview: str | None = None
    tmdb_vote_avg: float | None = None
    matched_film_id: str | None = None
    matched_title_zh: str | None = None
    matched_poster_url: str | None = None
    match_score: float | None = None
    created_at: datetime


class AwardOrg(BaseModel):
    org_id: str
    name_en: str
    name_zh: str
    tag_prefix: str
    official_url: str
    ceremony_month: int
    region: str
    scope: str
    priority: int
    note: str | None = None


# --- Feedback wiki models ---


class FeedbackPage(BaseModel):
    """A feedback wiki page — frontmatter + markdown body."""

    page_id: str  # relative path sans .md, e.g. "tags/thriller"
    kind: str
    title: str
    status: str = "open"  # open | done | dismissed | merged
    merged_into: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    updated_at: datetime | None = None
    model_used: str | None = None
    consultant_validated: bool = False
    confidence: float | None = None
    sources: list[str] = []
    body: str = ""


class ReanalyzeRequest(BaseModel):
    prompt: str = ""  # editor natural-language input; empty = generic re-validate
    use_consultant: bool = True


class ReanalyzeResponse(BaseModel):
    page_id: str
    frontmatter_updates: dict
    body_section_title: str
    body_section_md: str
    model_used: str
