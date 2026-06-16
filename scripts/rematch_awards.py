"""Re-run find_film_match over all award_nominees rows using current matcher logic.

Use after tightening MATCH_THRESHOLD / _normalise_title. Updates matched_film_id
and match_score in place; rows that no longer clear the threshold drop to NULL.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.db import get_db  # noqa: E402
from backend.film_matcher import MATCH_THRESHOLD, find_film_match  # noqa: E402


def main() -> None:
    cleared = 0
    updated = 0
    kept = 0
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, film_title_primary, film_title_alt, matched_film_id, match_score FROM award_nominees"
        ).fetchall()
        for r in rows:
            film_id, _title, score = find_film_match(
                conn, r["film_title_primary"], r["film_title_alt"]
            )
            new_match = film_id if score >= MATCH_THRESHOLD else None
            new_score = score if score >= MATCH_THRESHOLD else 0.0
            if (
                new_match == r["matched_film_id"]
                and abs(new_score - (r["match_score"] or 0)) < 1e-6
            ):
                kept += 1
                continue
            conn.execute(
                "UPDATE award_nominees SET matched_film_id=?, match_score=? WHERE id=?",
                (new_match, new_score, r["id"]),
            )
            if r["matched_film_id"] and not new_match:
                cleared += 1
            else:
                updated += 1
        conn.commit()

    print(f"re-match done: cleared={cleared} updated={updated} unchanged={kept} total={len(rows)}")


if __name__ == "__main__":
    main()
