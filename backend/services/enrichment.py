"""EnrichService — TMDb API data enrichment (movie + TV)."""

import json

import httpx

from backend.config import settings
from backend.services.base import BaseService

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_POSTER_PREFIX = "https://image.tmdb.org/t/p/w500"


class EnrichService(BaseService):
    """Enrich film metadata via TMDb API. Searches across movies + TV."""

    @property
    def name(self) -> str:
        return "enrichment"

    async def execute(self, input_data: dict) -> dict:
        title_zh = input_data.get("title_zh", "")
        title_en = input_data.get("title_en", "")
        queries = [q for q in (title_en, title_zh) if q]
        if not queries:
            return {"error": "No title provided"}

        async with httpx.AsyncClient(timeout=15) as client:
            hit = None
            for q in queries:
                hit = await self._search_multi(client, q, "zh-TW")
                if hit:
                    break
                hit = await self._search_multi(client, q, "en-US")
                if hit:
                    break
            if not hit:
                return {"error": f"No TMDb results for: {queries[0]}"}

            media_type = hit.get("media_type", "movie")
            details = await self._get_details(client, hit["id"], media_type)

        genres = [g["name"] for g in details.get("genres", [])]
        kw = details.get("keywords", {})
        kw_list = kw.get("keywords") or kw.get("results") or []
        keywords = [k["name"] for k in kw_list]
        credits = details.get("credits", {})
        cast = [c["name"] for c in credits.get("cast", [])[:5]]
        directors = [c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"]
        if not directors and media_type == "tv":
            directors = [c["name"] for c in details.get("created_by", [])]

        poster_path = details.get("poster_path")
        return {
            "tmdb_id": details["id"],
            "tmdb_media_type": media_type,
            # TMDB poster is geo-immune (unlike the CATCHPLAY+ og:image, which
            # the EU VPS can't reach) — used as the new-film poster fallback.
            "tmdb_poster_url": f"{TMDB_POSTER_PREFIX}{poster_path}" if poster_path else None,
            "tmdb_overview": details.get("overview", ""),
            "tmdb_genres": json.dumps(genres, ensure_ascii=False),
            "tmdb_keywords": json.dumps(keywords, ensure_ascii=False),
            "tmdb_vote_avg": details.get("vote_average"),
            "tmdb_cast": json.dumps(cast, ensure_ascii=False),
            "tmdb_director": directors[0] if directors else None,
        }

    async def _search_multi(self, client: httpx.AsyncClient, title: str, lang: str) -> dict | None:
        r = await client.get(
            f"{TMDB_BASE}/search/multi",
            params={"api_key": settings.tmdb_api_key, "query": title, "language": lang},
        )
        r.raise_for_status()
        for item in r.json().get("results", []):
            if item.get("media_type") in ("movie", "tv"):
                return item
        return None

    async def _get_details(self, client: httpx.AsyncClient, tmdb_id: int, media_type: str) -> dict:
        r = await client.get(
            f"{TMDB_BASE}/{media_type}/{tmdb_id}",
            params={
                "api_key": settings.tmdb_api_key,
                "language": "zh-TW",
                "append_to_response": "credits,keywords",
            },
        )
        r.raise_for_status()
        return r.json()
