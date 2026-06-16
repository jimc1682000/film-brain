"""Retry TMDB search using 繁→簡 conversion for films still missing tmdb_id.

TMDB stores PRC films under simplified titles only. Traditional-character search
returns empty; converting to simplified often resolves the match.
"""

import sys
import time
from pathlib import Path

import httpx
import zhconv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.db import get_db

TMDB_BASE = "https://api.themoviedb.org/3"
POSTER_PREFIX = "https://image.tmdb.org/t/p/w500"


def search_tmdb(client: httpx.Client, title: str) -> dict | None:
    r = client.get(
        f"{TMDB_BASE}/search/multi",
        params={"api_key": settings.tmdb_api_key, "query": title, "language": "zh-TW"},
    )
    if r.status_code != 200:
        return None
    for item in r.json().get("results", []):
        if item.get("media_type") in ("movie", "tv"):
            return item
    return None


def main():
    if not settings.tmdb_api_key:
        print("ERROR: TMDB_API_KEY not set")
        sys.exit(1)

    with get_db() as conn:
        rows = conn.execute(
            "SELECT film_id, title_zh, title_en FROM films WHERE tmdb_id IS NULL"
        ).fetchall()

    print(f"=== Retry {len(rows)} films with simplified Chinese ===")
    fixed = 0
    miss = 0

    with httpx.Client(timeout=15) as client:
        for i, f in enumerate(rows, 1):
            title_zh = f["title_zh"]
            title_simp = zhconv.convert(title_zh, "zh-cn")

            queries = []
            if title_simp != title_zh:
                queries.append(title_simp)
            if f["title_en"]:
                queries.append(f["title_en"])

            result = None
            for q in queries:
                result = search_tmdb(client, q)
                if result:
                    break

            if not result:
                miss += 1
                print(f"  [{i}/{len(rows)}] MISS: {title_zh}")
                continue

            poster = result.get("poster_path")
            poster_url = f"{POSTER_PREFIX}{poster}" if poster else None
            with get_db() as conn:
                if poster_url:
                    conn.execute(
                        "UPDATE films SET tmdb_id=?, poster_url=? WHERE film_id=?",
                        (result["id"], poster_url, f["film_id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE films SET tmdb_id=? WHERE film_id=?",
                        (result["id"], f["film_id"]),
                    )
            fixed += 1
            tmdb_title = result.get("title") or result.get("name")
            print(f"  [{i}/{len(rows)}] OK: {title_zh} → #{result['id']} ({tmdb_title})")
            time.sleep(0.25)

    print(f"\n=== Done: {fixed} fixed, {miss} missed ===")


if __name__ == "__main__":
    main()
