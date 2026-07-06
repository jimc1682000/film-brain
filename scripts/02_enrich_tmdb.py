"""Enrich films with TMDb data (genres, keywords, cast, director, overview)."""

import json
import re
import sys
import time
from pathlib import Path

import httpx
import zhconv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.db import get_db

TMDB_BASE = "https://api.themoviedb.org/3"
CACHE_DIR = settings.tmdb_cache_dir


def search_multi(client: httpx.Client, title: str, lang: str = "zh-TW") -> list[dict]:
    """Search TMDb across movies + TV. Returns movie/tv candidates in rank order."""
    r = client.get(
        f"{TMDB_BASE}/search/multi",
        params={
            "api_key": settings.tmdb_api_key,
            "query": title,
            "language": lang,
        },
    )
    r.raise_for_status()
    return [
        item for item in r.json().get("results", []) if item.get("media_type") in ("movie", "tv")
    ]


def find_by_imdb(client: httpx.Client, imdb_id: str) -> dict | None:
    """Authoritative lookup via CP-sourced imdb_id — no fuzzy collision possible."""
    r = client.get(
        f"{TMDB_BASE}/find/{imdb_id}",
        params={
            "api_key": settings.tmdb_api_key,
            "external_source": "imdb_id",
            "language": "zh-TW",
        },
    )
    r.raise_for_status()
    data = r.json()
    for key, mtype in (("movie_results", "movie"), ("tv_results", "tv")):
        if data.get(key):
            hit = data[key][0]
            hit["media_type"] = mtype
            return hit
    return None


def _norm_title(t: str) -> str:
    # zhconv: TMDb stores PRC titles simplified; CP titles are traditional.
    return re.sub(r"[\W_]+", "", zhconv.convert(t or "", "zh-cn")).lower()


def pick_candidate(
    candidates: list[dict],
    title_zh: str,
    title_en: str | None,
    release_year: int | None,
) -> dict | None:
    """Validate fuzzy-search candidates instead of trusting rank #1.

    A candidate must corroborate on title (zh-TW or original/en, normalized)
    or release year (±1). When CP has a release_year, candidates that
    contradict it are rejected outright — a wrong match poisons overview,
    cast and the film's embedding (e.g. 正義兄弟會 → Room).
    """
    want_titles = {_norm_title(title_zh)}
    if title_en:
        want_titles.add(_norm_title(title_en))
        want_titles.add(_norm_title(re.split(r"[:\-–]", title_en)[0]))
    want_titles.discard("")

    best, best_score = None, 0
    for c in candidates:
        cand_titles = {
            _norm_title(c.get("title") or c.get("name") or ""),
            _norm_title(c.get("original_title") or c.get("original_name") or ""),
        }
        cand_titles.discard("")
        date = c.get("release_date") or c.get("first_air_date") or ""
        cand_year = int(date[:4]) if date[:4].isdigit() else None

        title_ok = bool(want_titles & cand_titles)
        year_ok = (
            release_year is not None
            and cand_year is not None
            and abs(cand_year - release_year) <= 1
        )
        # Hard reject: CP year known, candidate year known, and they disagree.
        if release_year is not None and cand_year is not None and not year_ok:
            continue

        score = (2 if title_ok else 0) + (1 if year_ok else 0)
        if score > best_score:
            best, best_score = c, score
    return best


def get_details(client: httpx.Client, tmdb_id: int, media_type: str) -> dict:
    """Get full details (movie or tv) including credits + keywords."""
    # TV keywords live at /tv/{id}/keywords; append_to_response still works.
    r = client.get(
        f"{TMDB_BASE}/{media_type}/{tmdb_id}",
        params={
            "api_key": settings.tmdb_api_key,
            "language": "zh-TW",
            "append_to_response": "credits,keywords",
        },
    )
    r.raise_for_status()
    data = r.json()
    data["_media_type"] = media_type
    return data


def extract_enrichment(details: dict) -> dict:
    """Extract relevant fields from TMDb response (movie or tv)."""
    genres = [g["name"] for g in details.get("genres", [])]

    # TV: keywords.results. Movie: keywords.keywords.
    kw_data = details.get("keywords", {})
    kw_list = kw_data.get("keywords") or kw_data.get("results") or []
    keywords = [k["name"] for k in kw_list]

    credits = details.get("credits", {})
    cast = [c["name"] for c in credits.get("cast", [])[:5]]

    # TV: director role varies; fall back to created_by, else first crew Director.
    directors = [c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"]
    if not directors and details.get("_media_type") == "tv":
        directors = [c["name"] for c in details.get("created_by", [])]
    director = directors[0] if directors else None

    return {
        "tmdb_id": details["id"],
        "tmdb_overview": details.get("overview", ""),
        "tmdb_genres": json.dumps(genres, ensure_ascii=False),
        "tmdb_keywords": json.dumps(keywords, ensure_ascii=False),
        "tmdb_vote_avg": details.get("vote_average"),
        "tmdb_cast": json.dumps(cast, ensure_ascii=False),
        "tmdb_director": director,
    }


def clean_title_for_search(title_zh: str, title_en: str | None) -> list[str]:
    """Generate search queries — try English first, then Chinese."""
    queries = []
    if title_en:
        # Remove subtitle after colon or dash for better search
        clean = re.split(r"[:\-–]", title_en)[0].strip()
        queries.append(clean)
    queries.append(title_zh)
    # PRC titles live on TMDb in simplified only — trad search returns empty
    simp = zhconv.convert(title_zh, "zh-cn")
    if simp != title_zh:
        queries.append(simp)
    return queries


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not settings.tmdb_api_key:
        print("ERROR: TMDB_API_KEY not set in .env")
        sys.exit(1)

    with get_db() as conn:
        films = conn.execute(
            "SELECT film_id, title_zh, title_en, tmdb_id, imdb_id, release_year FROM films"
        ).fetchall()

    print(f"=== TMDb Enrichment: {len(films)} films ===")
    enriched = 0
    skipped = 0
    failed = 0

    with httpx.Client(timeout=15) as client:
        for i, film in enumerate(films):
            film_id = film["film_id"]
            cache_file = CACHE_DIR / f"{film_id}.json"

            # Skip if already enriched in DB
            if film["tmdb_id"]:
                skipped += 1
                continue

            # Check cache
            if cache_file.exists():
                with cache_file.open(encoding="utf-8") as f:
                    data = json.load(f)
                _update_film(film_id, data)
                enriched += 1
                continue

            # 1) Authoritative: CP-sourced imdb_id → /find (no fuzzy collision)
            result = None
            if film["imdb_id"]:
                result = find_by_imdb(client, film["imdb_id"])

            # 2) Fallback: fuzzy search, but validate candidates (title/year)
            if not result:
                queries = clean_title_for_search(film["title_zh"], film["title_en"])
                for q in queries:
                    for lang in ("zh-TW", "en-US"):
                        candidates = search_multi(client, q, lang=lang)
                        result = pick_candidate(
                            candidates, film["title_zh"], film["title_en"], film["release_year"]
                        )
                        if result:
                            break
                    if result:
                        break

            if not result:
                print(
                    f"  [{i + 1}/{len(films)}] NOT FOUND / no validated candidate: {film['title_zh']}"
                )
                failed += 1
                continue

            media_type = result.get("media_type", "movie")
            details = get_details(client, result["id"], media_type)
            data = extract_enrichment(details)

            # Cache response
            with cache_file.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Update DB
            _update_film(film_id, data)
            enriched += 1
            print(
                f"  [{i + 1}/{len(films)}] OK ({media_type}): "
                f"{film['title_zh']} → TMDb #{data['tmdb_id']}"
            )

            # Rate limit: TMDb allows ~40 req/10s
            time.sleep(0.3)

    print(f"\n=== Done: {enriched} enriched, {skipped} skipped, {failed} not found ===")


def _update_film(film_id: str, data: dict):
    """Update film record with TMDb data."""
    with get_db() as conn:
        conn.execute(
            "UPDATE films SET tmdb_id=?, tmdb_overview=?, tmdb_genres=?, "
            "tmdb_keywords=?, tmdb_vote_avg=?, tmdb_cast=?, tmdb_director=? "
            "WHERE film_id=?",
            (
                data["tmdb_id"],
                data["tmdb_overview"],
                data["tmdb_genres"],
                data["tmdb_keywords"],
                data["tmdb_vote_avg"],
                data["tmdb_cast"],
                data["tmdb_director"],
                film_id,
            ),
        )


if __name__ == "__main__":
    main()
