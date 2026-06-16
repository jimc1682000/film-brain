"""Re-run AutoTagService for films with ≤1 tags (never auto-tagged or stale).

These 1-tag films have only the migrated CATCHPLAY genre → makes their
embedding signal too narrow, hurting search relevance. Re-tag them so
embedding text gets the full AI tag set, then downstream re-embed picks up.
"""

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_db

BASE = "http://localhost:8000"
THRESHOLD = 0.7
CONCURRENCY = 3


def sparse_film_ids() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT f.film_id, f.title_zh
            FROM films f
            LEFT JOIN film_tags ft ON f.film_id = ft.film_id
            GROUP BY f.film_id
            HAVING COUNT(ft.tag_id) <= 1
            ORDER BY f.title_zh
            """
        ).fetchall()
    return [{"film_id": r["film_id"], "title_zh": r["title_zh"]} for r in rows]


async def tag_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, film: dict) -> dict:
    fid = film["film_id"]
    title = film.get("title_zh", fid)
    async with sem:
        try:
            r = await client.post(
                f"{BASE}/api/auto-tag/{fid}",
                params={"use_consultant": False, "locale": "zh_TW"},
                timeout=180,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return {"film_id": fid, "title": title, "error": str(e)}

        suggestions = [
            s for s in data.get("suggestions", []) if float(s.get("confidence") or 0) > THRESHOLD
        ]
        if not suggestions:
            return {"film_id": fid, "title": title, "saved": 0, "skipped": True}

        payload = {
            "suggestions": [
                {
                    "tag_id": s["tag_id"],
                    "confidence": float(s["confidence"]),
                    "reasoning": s.get("reasoning", ""),
                }
                for s in suggestions
            ],
            "source": "ai",
        }
        try:
            r = await client.post(f"{BASE}/api/auto-tag/{fid}/save", json=payload, timeout=30)
            r.raise_for_status()
            saved = r.json()
            return {
                "film_id": fid,
                "title": title,
                "saved": saved.get("total_saved", 0),
                "invalid": saved.get("invalid", []),
            }
        except Exception as e:
            return {"film_id": fid, "title": title, "error": f"save: {e}"}


async def main():
    films = sparse_film_ids()
    print(f"→ {len(films)} sparse-tag films, threshold > {THRESHOLD}, concurrency {CONCURRENCY}")
    if not films:
        return

    async with httpx.AsyncClient(follow_redirects=True) as client:
        sem = asyncio.Semaphore(CONCURRENCY)
        tasks = [tag_one(client, sem, f) for f in films]
        total_saved = 0
        errors = 0
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            res = await coro
            if "error" in res:
                errors += 1
                print(f"[{i}/{len(films)}] FAIL {res['title']}: {res['error']!r}")
            elif res.get("skipped"):
                print(f"[{i}/{len(films)}] — {res['title']}: no tags > {THRESHOLD}")
            else:
                saved_n = res.get("saved", 0)
                total_saved += saved_n
                inv = f" (invalid: {len(res.get('invalid', []))})" if res.get("invalid") else ""
                print(f"[{i}/{len(films)}] OK {res['title']}: saved {saved_n}{inv}")

        print(f"\n=== done: saved={total_saved}, errors={errors}, films={len(films)} ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(1)
