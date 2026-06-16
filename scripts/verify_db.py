"""Read-only library health report.

One place to answer "what's wrong with the data right now" before deciding
which repair to run. Prints counts only — never writes. Used by the
`library-doctor` skill (diagnose step) and as the post-pipeline sanity check
in `film-import-batch`.

Usage:
  python -m scripts.verify_db          # human-readable report
  python -m scripts.verify_db --json   # machine-readable (for the skill)
"""

from __future__ import annotations

import argparse
import json

from backend.db import get_db


def collect() -> dict:
    with get_db() as conn:
        q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        films = q("SELECT COUNT(*) FROM films")
        film_tags = q("SELECT COUNT(*) FROM film_tags")
        nominees = q("SELECT COUNT(*) FROM award_nominees")
        return {
            "films": films,
            "film_tags": film_tags,
            "award_nominees": nominees,
            # Posters that won't render: lazy-load data: URI, geo-gate landing
            # logo, or simply missing.
            "bad_posters": q(
                "SELECT COUNT(*) FROM films WHERE poster_url IS NULL "
                "OR poster_url LIKE 'data:%' "
                "OR poster_url LIKE '%global-landing%' OR poster_url LIKE '%/events/%'"
            ),
            # Films with ≤1 tag — only the migrated genre, embedding too narrow.
            "sparse_films": q(
                "SELECT COUNT(*) FROM ("
                "SELECT f.film_id FROM films f "
                "LEFT JOIN film_tags ft ON f.film_id = ft.film_id "
                "GROUP BY f.film_id HAVING COUNT(ft.tag_id) <= 1)"
            ),
            # Films whose description is the taxonomy-expansion placeholder
            # ("<title>｜CATCHPLAY+ 正版電影｜由 <genre> 分類擴充匯入。") — looks
            # populated but is meaningless. Originally written by an off-tree
            # ingest, missed by earlier backfills because it isn't NULL/''.
            "placeholder_descriptions": q(
                "SELECT COUNT(*) FROM films "
                "WHERE description LIKE '%｜CATCHPLAY+ 正版電影｜由%分類擴充匯入%'"
            ),
            "nominees_matched": q(
                "SELECT COUNT(*) FROM award_nominees WHERE matched_film_id IS NOT NULL"
            ),
            "nominees_unmatched": q(
                "SELECT COUNT(*) FROM award_nominees WHERE matched_film_id IS NULL"
            ),
            "distinct_matched_films": q(
                "SELECT COUNT(DISTINCT matched_film_id) FROM award_nominees "
                "WHERE matched_film_id IS NOT NULL"
            ),
            # Distinct films sharing one tmdb_id (beyond legit language
            # variants of the same title). The 5/26 batch wrote one tmdb_id
            # per 20-film chunk — 101 films carried another film's TMDb data
            # (e.g. 正義兄弟會 had Room's overview/cast). This catches that
            # corruption shape offline; the full per-row check is
            # scripts.verify_tmdb_match (semantic) + scripts.verify_tmdb_blind.
            "duplicate_tmdb_ids": q(
                "SELECT COUNT(*) FROM ("
                "SELECT tmdb_id FROM films WHERE tmdb_id IS NOT NULL "
                "GROUP BY tmdb_id "
                "HAVING COUNT(DISTINCT substr(title_zh, 1, 4)) > 1)"
            ),
        }


# (issue_key, label, repair hint shown in the report)
_REPAIRS = [
    ("bad_posters", "壞 / 缺海報", "scripts.fix_lazyload_posters --apply"),
    ("sparse_films", "tag ≤1 的片", "scripts.autotag_sparse_films"),
    ("placeholder_descriptions", "佔位簡介(沒抓 CATCHPLAY+ 真劇情)", "scripts.backfill_catchplay"),
    (
        "duplicate_tmdb_ids",
        "多片共用 tmdb_id(錯配)",
        "scripts.verify_tmdb_match / scripts.verify_tmdb_blind 找出錯列後重 enrich",
    ),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    stats = collect()
    if args.json:
        print(json.dumps(stats, ensure_ascii=False))
        return 0

    print("=== 片庫健檢 ===")
    print(f"films                : {stats['films']}")
    print(f"film_tags            : {stats['film_tags']}")
    print(f"award_nominees       : {stats['award_nominees']}")
    print(
        f"  matched            : {stats['nominees_matched']} "
        f"→ {stats['distinct_matched_films']} 部片"
    )
    print(f"  unmatched          : {stats['nominees_unmatched']}")
    print("--- 需修復 ---")
    for key, label, hint in _REPAIRS:
        n = stats[key]
        flag = "⚠️ " if n else "✅ "
        print(f"{flag}{label:12}: {n}" + (f"   → {hint}" if n else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
