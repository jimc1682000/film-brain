"""Awards dashboard read-model assembly.

The /api/awards endpoints were doing meaningful business work inline —
batch + ceremony-metric joins, nominee + matched-film stitching, tag
label lookups. None of that is HTTP-shaped; it belongs alongside the
other award domain logic in this module so the router can shrink to
parameter parsing + serialization.
"""

from __future__ import annotations

import sqlite3

from backend.db import (
    get_award_nominees,
    get_ceremony_metrics,
    get_recent_award_batches_v2,
)
from backend.models import AwardBatchSummary, AwardNominee


def list_recent_batches(conn: sqlite3.Connection, limit: int) -> list[AwardBatchSummary]:
    """Latest (tag, year) batches with ceremony-level distinct-film counts.

    `recent_batches` previously fetched the tag label one-by-one in the
    request loop. Same effect achieved here in a single batched JOIN so
    the route is just `with get_db() as conn: return list_recent_batches(conn, limit)`.
    """
    rows = get_recent_award_batches_v2(conn, limit=limit)
    ceremony_metrics = get_ceremony_metrics(conn)

    tag_ids = [r["tag_id"] for r in rows]
    label_map: dict[str, str] = {}
    if tag_ids:
        qmarks = ",".join("?" for _ in tag_ids)
        for tr in conn.execute(
            f"SELECT tag_id, label_zh_tw FROM tags WHERE tag_id IN ({qmarks})",
            tag_ids,
        ).fetchall():
            label_map[tr["tag_id"]] = tr["label_zh_tw"]

    out: list[AwardBatchSummary] = []
    for r in rows:
        m = ceremony_metrics.get((r["org_id"], r["year"]), {})
        out.append(
            AwardBatchSummary(
                tag_id=r["tag_id"],
                tag_label_zh_tw=label_map.get(r["tag_id"]) or r["tag_id"],
                year=r["year"] or 0,
                org_id=r["org_id"],
                total_count=r["total_count"],
                matched_count=r["matched_count"],
                ceremony_nominated_films_total=m.get("nominated_films_total", 0),
                ceremony_won_films_total=m.get("won_films_total", 0),
                ceremony_nominated_films_matched=m.get("nominated_films_matched", 0),
                ceremony_won_films_matched=m.get("won_films_matched", 0),
                latest_insert=r["latest_insert"],
            )
        )
    return out


def list_nominees_with_films(
    conn: sqlite3.Connection,
    *,
    tag_id: str | None = None,
    org_id: str | None = None,
    year: int | None = None,
    film_id: str | None = None,
    limit: int = 200,
) -> list[AwardNominee]:
    """Stitch nominee rows with matched-film metadata (title / poster).

    Single batched fetch on matched film_ids replaces the per-row
    follow-up SELECTs the router used to do. Output shape matches the
    AwardNominee pydantic model so the route is a one-liner.
    """
    rows = get_award_nominees(
        conn, tag_id=tag_id, org_id=org_id, year=year, film_id=film_id, limit=limit
    )

    matched_ids = [r["matched_film_id"] for r in rows if r.get("matched_film_id")]
    film_rows: dict[str, dict] = {}
    if matched_ids:
        qmarks = ",".join("?" for _ in matched_ids)
        for fr in conn.execute(
            f"SELECT film_id, title_zh, title_en, poster_url FROM films WHERE film_id IN ({qmarks})",
            matched_ids,
        ).fetchall():
            film_rows[fr["film_id"]] = dict(fr)

    out: list[AwardNominee] = []
    for r in rows:
        matched = film_rows.get(r["matched_film_id"]) if r.get("matched_film_id") else None
        out.append(
            AwardNominee(
                id=r["id"],
                org_id=r["org_id"],
                tag_id=r["tag_id"],
                year=r["year"],
                category=r["category"],
                film_title_primary=r["film_title_primary"],
                film_title_alt=r.get("film_title_alt"),
                person=r.get("person"),
                result=r["result"],
                tmdb_id=r.get("tmdb_id"),
                tmdb_media_type=r.get("tmdb_media_type"),
                tmdb_title=r.get("tmdb_title"),
                tmdb_year=r.get("tmdb_year"),
                tmdb_poster_url=r.get("tmdb_poster_url"),
                tmdb_overview=r.get("tmdb_overview"),
                tmdb_vote_avg=r.get("tmdb_vote_avg"),
                matched_film_id=r.get("matched_film_id"),
                matched_title_zh=(matched or {}).get("title_zh"),
                matched_poster_url=(matched or {}).get("poster_url"),
                match_score=r.get("match_score"),
                created_at=r["created_at"],
            )
        )
    return out
