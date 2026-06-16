"""Award-tracker domain logic: org registry + tag materialization.

Title-similarity matching lives in `backend/film_matcher.py`. This module
focuses on the awards-specific orchestration: convert an org+category
into our `tags` row, decide which CATCHPLAY+ film a nominee maps to,
resolve the TMDB metadata authoritatively (by film tmdb_id when matched,
fall back to title search), and persist the nominee row + curation tag.
"""

from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from backend.config import settings
from backend.db import insert_film_tag, insert_tag, upsert_award_nominee
from backend.film_matcher import MATCH_THRESHOLD, find_film_match
from backend.tmdb_lookup import fetch_tmdb_by_id, search_tmdb

REGISTRY_PATH: Path = settings.data_dir / "awards-registry.json"


@lru_cache(maxsize=1)
def load_orgs() -> dict[str, dict]:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {org["org_id"]: org for org in data.get("orgs", [])}


def get_org(org_id: str) -> dict:
    orgs = load_orgs()
    if org_id not in orgs:
        raise KeyError(f"Unknown award org_id: {org_id}")
    return orgs[org_id]


# --- Tag materialization ----------------------------------------------


def slugify_category(category: str) -> str:
    """Normalise award category to tag_id suffix."""
    s = category.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "category"


def build_tag_id(org: dict, category: str) -> str:
    return f"{org['tag_prefix']}-{slugify_category(category)}"


def build_tag_labels(org: dict, category: str) -> tuple[str, str]:
    cat = category.strip()
    return (f"{org['name_en']} — {cat}", f"{org['name_zh']} — {cat}")


def register_award_tag(conn: sqlite3.Connection, org: dict, category: str) -> str:
    """Ensure (tags) row exists for an award category; returns tag_id."""
    tag_id = build_tag_id(org, category)
    label_en, label_zh_tw = build_tag_labels(org, category)
    insert_tag(
        conn,
        tag_id=tag_id,
        dimension="award",
        label_en=label_en,
        label_zh_tw=label_zh_tw,
        source="award-tracker",
    )
    return tag_id


def register_curation_award_tag(conn: sqlite3.Connection, org: dict, year: int, result: str) -> str:
    """Ensure a curation-level (org + year + won/nominated) tag row exists.

    Curation tags describe "this film was selected by <award>" — the
    selection is the signal, not a film property. Kept separate from the
    14-dim semantic taxonomy via dimension='curation-award'.
    """
    role = "winner" if (result or "").lower() == "won" else "nominee"
    tag_id = f"curation-{org['tag_prefix']}-{year}-{role}"
    role_en = "Winner" if role == "winner" else "Nominee"
    role_zh = "得獎" if role == "winner" else "入圍"
    insert_tag(
        conn,
        tag_id=tag_id,
        dimension="curation-award",
        label_en=f"{org['name_en']} {year} {role_en}",
        label_zh_tw=f"{org['name_zh']} {year} {role_zh}",
        source="award-tracker",
    )
    return tag_id


# --- TMDB + curation tag stitching helpers ----------------------------


def _film_tmdb_and_year(conn: sqlite3.Connection, film_id: str) -> tuple[int | None, int | None]:
    """(tmdb_id, release_year) for a film, tolerating older schemas.

    release_year is added by a later ALTER on prod DB; on schemas without it
    (e.g. a test DB created via init_db only) select tmdb_id alone and report
    release_year as None.
    """
    try:
        row = conn.execute(
            "SELECT tmdb_id, release_year FROM films WHERE film_id = ?", (film_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        row = conn.execute("SELECT tmdb_id FROM films WHERE film_id = ?", (film_id,)).fetchone()
    if not row:
        return None, None
    try:
        release_year = row["release_year"]
    except (IndexError, KeyError):
        release_year = None
    return row["tmdb_id"], release_year


def _resolve_tmdb_for_nominee(
    conn: sqlite3.Connection,
    matched_film_id: str | None,
    primary_title: str,
    alt_title: str | None,
) -> tuple[dict | None, int | None]:
    """Return (tmdb_payload, release_year).

    When we own the matched film, the films table has the authoritative
    tmdb_id — fetch by id to avoid the 進行曲/ICHU class of title collision.
    Only fall back to search_tmdb (gated on release_year + similarity) for
    nominees not in our library.
    """
    release_year: int | None = None
    if matched_film_id:
        tmdb_id, release_year = _film_tmdb_and_year(conn, matched_film_id)
        if tmdb_id:
            tmdb = fetch_tmdb_by_id(int(tmdb_id))
            if tmdb:
                return tmdb, release_year

    for candidate in (primary_title, alt_title):
        if not candidate:
            continue
        tmdb = search_tmdb(candidate, release_year=release_year)
        if tmdb:
            return tmdb, release_year
    return None, release_year


def _apply_curation_tag(
    conn: sqlite3.Connection, org: dict, year: int, result: str, matched_film_id: str
) -> None:
    """Attach the per-ceremony curation-award tag to a matched film.

    One tag per (org, year, won/nominated). Per-category awards (Best
    Director, etc.) live in the `award_nominees` table and never as film
    tags — that would pollute the semantic taxonomy.
    """
    curation_tag = register_curation_award_tag(conn, org, year, result)
    insert_film_tag(
        conn,
        film_id=matched_film_id,
        tag_id=curation_tag,
        confidence=1.0,
        source="award-curation",
        award_year=year,
        award_result=result,
    )


# --- Orchestration entry point ----------------------------------------


def record_nomination(
    conn: sqlite3.Connection,
    org: dict,
    year: int,
    category: str,
    primary_title: str,
    alt_title: str | None,
    person: str | None,
    result: str,
    source_url: str | None = None,
    ceremony_date: str | None = None,
) -> dict:
    """Persist one nominee: tag → match → tmdb → upsert → curation tag."""
    tag_id = register_award_tag(conn, org, category)

    film_id, matched_title, score = find_film_match(conn, primary_title, alt_title)
    matched_film_id = film_id if film_id and score >= MATCH_THRESHOLD else None

    tmdb, _release_year = _resolve_tmdb_for_nominee(conn, matched_film_id, primary_title, alt_title)

    nominee_row: dict = {
        "org_id": org["org_id"],
        "tag_id": tag_id,
        "year": year,
        "category": category,
        "film_title_primary": primary_title,
        "film_title_alt": alt_title,
        "person": person,
        "result": result,
        "source_url": source_url,
        "ceremony_date": ceremony_date,
        "matched_film_id": matched_film_id,
        "match_score": score,
    }
    if tmdb:
        nominee_row.update(tmdb)
    upsert_award_nominee(conn, **nominee_row)

    if matched_film_id:
        _apply_curation_tag(conn, org, year, result, matched_film_id)

    return {
        "category": category,
        "film_title": primary_title,
        "person": person,
        "result": result,
        "tag_id": tag_id,
        "matched_film_id": matched_film_id,
        "matched_title": matched_title if matched_film_id else None,
        "match_score": score,
        "tmdb": tmdb,
    }
