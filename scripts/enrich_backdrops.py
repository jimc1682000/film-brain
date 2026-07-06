"""Backfill films.tmdb_backdrop_url with the wide TMDb backdrop (w1280).

Feeds the MUBI-style detail hero a real cinematic still instead of the blurred
poster fallback. Idempotent: adds the column if missing and only fetches films
that have a tmdb_id but no backdrop yet, so re-runs are cheap. Films don't store
the TMDb media_type, so we try /movie then /tv.
"""

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.db import get_db
from backend.tmdb_lookup import BACKDROP_PREFIX

TMDB_BASE = "https://api.themoviedb.org/3"


def _ensure_column(conn) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(films)").fetchall()]
    if "tmdb_backdrop_url" not in cols:
        conn.execute("ALTER TABLE films ADD COLUMN tmdb_backdrop_url TEXT")
        print("added column films.tmdb_backdrop_url")


def _backdrop(client: httpx.Client, tmdb_id: int) -> str | None:
    for mt in ("movie", "tv"):
        r = client.get(
            f"{TMDB_BASE}/{mt}/{tmdb_id}",
            params={"api_key": settings.tmdb_api_key, "language": "zh-TW"},
        )
        if r.status_code == 200:
            bp = r.json().get("backdrop_path")
            return f"{BACKDROP_PREFIX}{bp}" if bp else None
        if r.status_code == 404:
            continue  # wrong media type — try the other
        r.raise_for_status()
    return None


def main() -> None:
    if not settings.tmdb_api_key:
        sys.exit("TMDB_API_KEY not set")
    with get_db() as conn:
        _ensure_column(conn)
        rows = conn.execute(
            "SELECT film_id, tmdb_id FROM films "
            "WHERE tmdb_id IS NOT NULL "
            "AND (tmdb_backdrop_url IS NULL OR tmdb_backdrop_url = '')"
        ).fetchall()
        total = len(rows)
        print(f"{total} films to backfill")
        ok = miss = err = 0
        with httpx.Client(timeout=15.0) as client:
            for i, row in enumerate(rows):
                try:
                    url = _backdrop(client, row["tmdb_id"])
                except httpx.HTTPError as e:
                    err += 1
                    print(f"  [{i + 1}/{total}] {row['film_id']} ERROR {e}")
                    continue
                if url:
                    conn.execute(
                        "UPDATE films SET tmdb_backdrop_url = ? WHERE film_id = ?",
                        (url, row["film_id"]),
                    )
                    ok += 1
                else:
                    miss += 1
                if (i + 1) % 25 == 0:
                    conn.commit()
                    print(f"  [{i + 1}/{total}] ok={ok} no_backdrop={miss} err={err}")
                time.sleep(0.05)
        print(f"done: {ok} backdrops stored, {miss} without, {err} errors")


if __name__ == "__main__":
    main()
