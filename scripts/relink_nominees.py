"""Re-match unmatched award_nominees rows against the current films table.

Whenever new films land in the library (e.g. after a film-import-batch
expansion), pre-existing nominee rows whose `matched_film_id` was NULL
may now have a corresponding CATCHPLAY+ film. This script walks the
unmatched rows, runs `find_film_match`, and on a hit:

  - sets matched_film_id + match_score on award_nominees
  - re-pulls the TMDB metadata using the film's authoritative tmdb_id
    (avoids the search-by-title fuzzy collisions handled in tmdb_lookup)
  - inserts the canonical curation-award tag into film_tags

Read-only by default. Pass --apply to write.

Usage:
  uv run python -m scripts.relink_nominees           # dry-run report
  uv run python -m scripts.relink_nominees --apply   # commit changes
"""

from __future__ import annotations

import argparse
import sys

from backend.award_manager import get_org, register_curation_award_tag
from backend.db import get_db, insert_film_tag
from backend.film_matcher import MATCH_THRESHOLD, find_film_match
from backend.tmdb_lookup import fetch_tmdb_by_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write fixes")
    args = parser.parse_args(argv)

    with get_db() as conn:
        unmatched = conn.execute(
            "SELECT id, org_id, tag_id, year, category, film_title_primary, "
            "       film_title_alt, result "
            "FROM award_nominees WHERE matched_film_id IS NULL"
        ).fetchall()
        unmatched = [dict(r) for r in unmatched]
        print(f"Scanning {len(unmatched)} unmatched nominee rows…")

        hits: list[dict] = []
        for nom in unmatched:
            film_id, matched_title, score = find_film_match(
                conn, nom["film_title_primary"], nom.get("film_title_alt")
            )
            if not film_id or score < MATCH_THRESHOLD:
                continue
            hits.append(
                {**nom, "matched_film_id": film_id, "matched_title": matched_title, "score": score}
            )

        if not hits:
            print("No new matches found.")
            return 0

        print(f"\nFound {len(hits)} new matches:")
        for h in hits[:30]:
            print(
                f"  id={h['id']} {h['year']} {h['tag_id']:35s} "
                f"'{h['film_title_primary']}' → {h['matched_title']} (score={h['score']:.2f})"
            )
        if len(hits) > 30:
            print(f"  ... and {len(hits) - 30} more")

        if not args.apply:
            print("\nDry-run. Re-run with --apply to write.")
            return 0

        updated = 0
        for h in hits:
            row = conn.execute(
                "SELECT tmdb_id FROM films WHERE film_id = ?", (h["matched_film_id"],)
            ).fetchone()
            tmdb = fetch_tmdb_by_id(int(row["tmdb_id"])) if row and row["tmdb_id"] else None

            params = {
                "matched_film_id": h["matched_film_id"],
                "match_score": h["score"],
            }
            if tmdb:
                params.update(tmdb)
            set_clause = ", ".join(f"{k} = ?" for k in params)
            conn.execute(
                f"UPDATE award_nominees SET {set_clause} WHERE id = ?",
                (*params.values(), h["id"]),
            )

            # Apply curation-award tag onto film_tags (idempotent due to INSERT OR REPLACE).
            # `register_curation_award_tag` needs the full org dict from
            # awards-registry.json (carries name_en + tag_prefix).
            org = get_org(h["org_id"])
            if not org:
                print(f"  curation tag for {h['id']}: org {h['org_id']} not in registry")
                continue
            try:
                curation_tag = register_curation_award_tag(conn, org, h["year"], h["result"])
                insert_film_tag(
                    conn,
                    film_id=h["matched_film_id"],
                    tag_id=curation_tag,
                    confidence=1.0,
                    source="award-curation",
                    award_year=h["year"],
                    award_result=h["result"],
                )
            except Exception as e:
                print(f"  curation tag for {h['id']} failed: {e}")

            updated += 1

        conn.commit()
        print(f"\nApplied {updated} updates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
