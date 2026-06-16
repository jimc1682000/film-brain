"""Backfill curation-award tags for already-matched award_nominees rows.

Run once after switching record_nomination from per-category film_tags inserts
to curation-level (org + year + won/nominated) tags.
"""

from backend.award_manager import get_org, register_curation_award_tag
from backend.db import get_db, insert_film_tag


def main() -> None:
    stats = {"tags_created": set(), "film_tags_inserted": 0, "nominees_seen": 0}
    with get_db() as conn:
        rows = conn.execute(
            "SELECT org_id, year, result, matched_film_id "
            "FROM award_nominees WHERE matched_film_id IS NOT NULL"
        ).fetchall()
        for r in rows:
            stats["nominees_seen"] += 1
            try:
                org = get_org(r["org_id"])
            except KeyError:
                continue
            curation_tag = register_curation_award_tag(conn, org, r["year"], r["result"])
            stats["tags_created"].add(curation_tag)
            insert_film_tag(
                conn,
                film_id=r["matched_film_id"],
                tag_id=curation_tag,
                confidence=1.0,
                source="award-curation",
                award_year=r["year"],
                award_result=r["result"],
            )
            stats["film_tags_inserted"] += 1
    print(
        f"nominees seen={stats['nominees_seen']}, "
        f"curation tags={len(stats['tags_created'])}, "
        f"film_tags upserts={stats['film_tags_inserted']}"
    )


if __name__ == "__main__":
    main()
