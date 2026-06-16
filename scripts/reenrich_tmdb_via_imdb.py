"""Re-enrich TMDb data using imdb_id as authoritative key (not title fuzzy).

For every film with a CATCHPLAY-sourced imdb_id, call TMDb /find with
external_source=imdb_id → authoritative tmdb_id. Compare against the old
tmdb_id; log the mismatches (these are the prior mis-matches) and overwrite.
"""

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.db import get_db

TMDB_BASE = "https://api.themoviedb.org/3"


def tmdb_find_by_imdb(client: httpx.Client, imdb_id: str) -> dict | None:
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
    for key in ("movie_results", "tv_results"):
        if data.get(key):
            hit = data[key][0]
            hit["_media_type"] = "movie" if key == "movie_results" else "tv"
            return hit
    return None


def tmdb_details(client: httpx.Client, tmdb_id: int, media_type: str) -> dict:
    r = client.get(
        f"{TMDB_BASE}/{media_type}/{tmdb_id}",
        params={
            "api_key": settings.tmdb_api_key,
            "language": "zh-TW",
            "append_to_response": "credits,keywords,external_ids",
        },
    )
    r.raise_for_status()
    d = r.json()
    d["_media_type"] = media_type
    return d


def extract(details: dict) -> dict:
    genres = [g["name"] for g in details.get("genres", [])]
    kw_data = details.get("keywords") or {}
    kw_list = kw_data.get("keywords") or kw_data.get("results") or []
    keywords = [k["name"] for k in kw_list]
    credits = details.get("credits") or {}
    cast = [c["name"] for c in credits.get("cast", [])[:5]]
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


def main() -> None:
    if not settings.tmdb_api_key:
        print("ERROR: TMDB_API_KEY not set")
        sys.exit(1)

    with get_db() as conn:
        rows = conn.execute(
            "SELECT film_id, title_zh, imdb_id, tmdb_id FROM films WHERE imdb_id IS NOT NULL"
        ).fetchall()

    total = len(rows)
    print(f"=== IMDb-keyed TMDb re-enrich: {total} films ===")

    stats = {
        "matched_same": 0,
        "matched_changed": 0,
        "tmdb_not_found": 0,
        "changes": [],
    }

    with httpx.Client(timeout=15) as client, get_db() as conn:
        for i, row in enumerate(rows):
            imdb = row["imdb_id"]
            old_tmdb = row["tmdb_id"]
            hit = tmdb_find_by_imdb(client, imdb)
            if not hit:
                stats["tmdb_not_found"] += 1
                print(f"  [{i + 1}/{total}] TMDb no find for {row['title_zh']} ({imdb})")
                # Also clear wrong tmdb_id if we had one — since it's unverified now.
                if old_tmdb:
                    conn.execute(
                        "UPDATE films SET tmdb_id=NULL, tmdb_overview=NULL, "
                        "tmdb_genres=NULL, tmdb_keywords=NULL, tmdb_vote_avg=NULL, "
                        "tmdb_cast=NULL, tmdb_director=NULL WHERE film_id=?",
                        (row["film_id"],),
                    )
                continue

            details = tmdb_details(client, hit["id"], hit["_media_type"])
            data = extract(details)
            new_tmdb = data["tmdb_id"]

            if old_tmdb and old_tmdb != new_tmdb:
                stats["matched_changed"] += 1
                stats["changes"].append(
                    {
                        "film_id": row["film_id"],
                        "title": row["title_zh"],
                        "imdb": imdb,
                        "old_tmdb": old_tmdb,
                        "new_tmdb": new_tmdb,
                    }
                )
                print(
                    f"  [{i + 1}/{total}] CHANGE {row['title_zh']}: "
                    f"tmdb {old_tmdb} → {new_tmdb} (via {imdb})"
                )
            else:
                stats["matched_same"] += 1

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
                    row["film_id"],
                ),
            )
            if (i + 1) % 25 == 0:
                conn.commit()
            time.sleep(0.25)
        conn.commit()

    print(
        f"\n=== Done: same={stats['matched_same']}, "
        f"changed={stats['matched_changed']}, "
        f"tmdb_not_found={stats['tmdb_not_found']} ==="
    )

    if stats["changes"]:
        print("\nChanged tmdb_id (these are the prior mis-matches):")
        for c in stats["changes"]:
            print(f"  {c['title']}: {c['old_tmdb']} → {c['new_tmdb']}  (imdb {c['imdb']})")


if __name__ == "__main__":
    main()
