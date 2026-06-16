"""Verify tmdb_id for films the semantic screen can't see (no tmdb_overview).

For each film with a tmdb_id but empty tmdb_overview, fetch the TMDb record
by id and corroborate title (zh-TW / original / en, normalized) or release
year (±1) against CP data. Read-only; prints suspects.

Usage:
    python -m scripts.verify_tmdb_blind [--json docs/reports/tmdb-blind-report.json]
"""

import argparse
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


def _norm(t: str) -> str:
    # zhconv: TMDb stores PRC titles simplified; CP titles are traditional.
    return re.sub(r"[\W_]+", "", zhconv.convert(t or "", "zh-cn")).lower()


def fetch(client: httpx.Client, tmdb_id: int) -> list[dict]:
    """Return BOTH movie and tv records when the id exists in both namespaces.

    films.tmdb_id stores no media_type, so an id is ambiguous — checking only
    one namespace false-positives (e.g. 靈犬雪麗: movie #10108 is D.C. Sniper
    but tv #10108 is the correct Belle and Sebastian).
    """
    out = []
    for mtype in ("movie", "tv"):
        r = client.get(
            f"{TMDB_BASE}/{mtype}/{tmdb_id}",
            params={"api_key": settings.tmdb_api_key, "language": "zh-TW"},
        )
        if r.status_code == 200:
            d = r.json()
            d["_media_type"] = mtype
            out.append(d)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    with get_db() as conn:
        rows = conn.execute(
            "SELECT film_id, title_zh, title_en, tmdb_id, imdb_id, release_year "
            "FROM films WHERE tmdb_id IS NOT NULL "
            "AND (tmdb_overview IS NULL OR tmdb_overview='')"
        ).fetchall()

    print(f"=== blind check: {len(rows)} films (tmdb_id, no overview) ===")
    suspects, ok = [], 0
    with httpx.Client(timeout=15) as client:
        adjudicated = 0
        for i, row in enumerate(rows):
            # apply_tmdb_match marks hand-adjudicated alias matches in the
            # cache — corroboration would fail on them forever, skip.
            cache = settings.tmdb_cache_dir / f"{row['film_id']}.json"
            if cache.exists():
                try:
                    cached = json.loads(cache.read_text())
                except ValueError:
                    cached = {}
                if cached.get("adjudicated") and cached.get("tmdb_id") == row["tmdb_id"]:
                    adjudicated += 1
                    ok += 1
                    continue
            records = fetch(client, row["tmdb_id"])
            time.sleep(0.25)
            if not records:
                suspects.append({**dict(row), "reason": "tmdb_id not found on TMDb"})
                continue

            # CP titles often carry suffixes like "(國語)" — strip parens
            zh = re.sub(r"[（(].*?[)）]", "", row["title_zh"])
            want = {_norm(zh), _norm(row["title_en"] or "")}
            want.discard("")

            # The id is media-type-ambiguous: pass if EITHER record corroborates
            corroborated = False
            seen = []
            for d in records:
                cand_titles = {
                    _norm(d.get("title") or d.get("name") or ""),
                    _norm(d.get("original_title") or d.get("original_name") or ""),
                }
                cand_titles.discard("")
                date = d.get("release_date") or d.get("first_air_date") or ""
                cand_year = int(date[:4]) if date[:4].isdigit() else None
                seen.append(f"{d['_media_type']}:{d.get('title') or d.get('name')} [{cand_year}]")

                title_ok = bool(want & cand_titles)
                year_ok = (
                    row["release_year"] is not None
                    and cand_year is not None
                    and abs(cand_year - row["release_year"]) <= 1
                )
                if title_ok or year_ok:
                    corroborated = True
                    break
            if corroborated:
                ok += 1
            else:
                suspects.append(
                    {
                        **dict(row),
                        "tmdb_title": " ; ".join(seen),
                        "tmdb_year": None,
                        "reason": "no title/year corroboration",
                    }
                )
            if (i + 1) % 25 == 0:
                print(f"  checked {i + 1}/{len(rows)}", flush=True)

    print(f"\n=== ok={ok} (adjudicated-skip {adjudicated}), suspects={len(suspects)} ===")
    for s in suspects:
        print(
            f"  {s['title_zh']} (tmdb {s['tmdb_id']}) → "
            f"{s.get('tmdb_title')} [{s.get('tmdb_year')}] — {s['reason']}"
        )
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(suspects, ensure_ascii=False, indent=2))
        print(f"Report → {args.json_path}")


if __name__ == "__main__":
    main()
