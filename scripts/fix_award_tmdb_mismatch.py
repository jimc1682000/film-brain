"""Backfill: re-pull TMDB metadata for award_nominees rows where matched_film_id
already exists, using the CATCHPLAY+ films.tmdb_id as the authoritative id.

Why: the legacy ingest used search_tmdb (popularity-ranked title search) which
matched the wrong film for ambiguous titles. Example: 進行曲 (Marching Boys,
TMDB 1222574) ended up paired with ICHU 偶像進行曲 (anime, TMDB 112667) in
award_nominees, so the awards page rendered the anime poster.

Usage:
  uv run python -m scripts.fix_award_tmdb_mismatch          # report
  uv run python -m scripts.fix_award_tmdb_mismatch --apply  # write fixes

Only touches rows where films.tmdb_id != award_nominees.tmdb_id. Rows without
matched_film_id (i.e. nominee not in CATCHPLAY+ library) are left alone.
"""

from __future__ import annotations

import argparse
import sys

from backend.db import get_db
from backend.tmdb_lookup import fetch_tmdb_by_id


def find_mismatches(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT an.id, an.tag_id, an.year, an.film_title_primary, "
        "       an.matched_film_id, an.tmdb_id AS award_tmdb_id, "
        "       an.tmdb_title AS award_tmdb_title, "
        "       f.tmdb_id AS film_tmdb_id, f.title_zh "
        "FROM award_nominees an "
        "JOIN films f ON f.film_id = an.matched_film_id "
        "WHERE f.tmdb_id IS NOT NULL "
        "  AND (an.tmdb_id IS NULL OR an.tmdb_id != f.tmdb_id) "
        "ORDER BY an.year DESC, an.tag_id"
    ).fetchall()
    return [dict(r) for r in rows]


def apply_fix(conn, row: dict) -> bool:
    tmdb = fetch_tmdb_by_id(int(row["film_tmdb_id"]))
    if not tmdb:
        return False
    conn.execute(
        "UPDATE award_nominees SET "
        "  tmdb_id = ?, tmdb_media_type = ?, tmdb_title = ?, tmdb_original_title = ?, "
        "  tmdb_year = ?, tmdb_poster_url = ?, tmdb_overview = ?, tmdb_vote_avg = ? "
        "WHERE id = ?",
        (
            tmdb["tmdb_id"],
            tmdb["tmdb_media_type"],
            tmdb["tmdb_title"],
            tmdb["tmdb_original_title"],
            tmdb["tmdb_year"],
            tmdb["tmdb_poster_url"],
            tmdb["tmdb_overview"],
            tmdb["tmdb_vote_avg"],
            row["id"],
        ),
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write fixes instead of dry-run")
    parser.add_argument("--limit", type=int, default=None, help="Cap on rows processed")
    args = parser.parse_args(argv)

    with get_db() as conn:
        mismatches = find_mismatches(conn)
        if args.limit:
            mismatches = mismatches[: args.limit]

        if not mismatches:
            print("No mismatches found. award_nominees.tmdb_id == films.tmdb_id everywhere.")
            return 0

        print(f"Found {len(mismatches)} mismatched rows:")
        for r in mismatches[:20]:
            print(
                f"  id={r['id']} year={r['year']} tag={r['tag_id']:30s} "
                f"film='{r['title_zh']}' award_tmdb={r['award_tmdb_id']} "
                f"-> film_tmdb={r['film_tmdb_id']} (was '{r['award_tmdb_title']}')"
            )
        if len(mismatches) > 20:
            print(f"  ... and {len(mismatches) - 20} more")

        if not args.apply:
            print("\nDry-run. Re-run with --apply to write fixes.")
            return 0

        fixed = 0
        for r in mismatches:
            if apply_fix(conn, r):
                fixed += 1
        conn.commit()
        print(f"\nApplied {fixed}/{len(mismatches)} fixes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
