"""Seed the system from a neutral films.seed.json (bring-your-own-films).

Brand-neutral: any dataset matching data/films.seed.schema.json populates the
system — CATCHPLAY ingest is just one private source adapter that emits this
format. Replaces the old CATCHPLAY-coupled `seed_all` pipeline for the public
repo.

    python -m scripts.seed_from_file data/films.seed.json [--auto-tag] [--compute-similar]

Tags are three-state:
  * present            -> written as film_tags (keyless).
  * absent + --auto-tag -> filled by the LLM AutoTagService (needs a backend).
  * absent, no flag    -> seeded with zero tags (description still embeds; search
                          works without tag boosts). Warned, never blocks.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from backend.config import settings
from backend.db import init_db, insert_film, insert_film_tag, insert_tag
from backend.tag_registry import TagRegistry

# Locale order used to resolve the primary (display/embed) title from the map.
_TITLE_LOCALES = ("zh", "en")


def primary_title(titles: dict[str, str]) -> str:
    """Resolve the display title from a locale->title map (zh -> en -> first)."""
    for loc in _TITLE_LOCALES:
        if titles.get(loc):
            return titles[loc]
    return next(iter(titles.values()))


def parse_film(raw: dict, valid_tag_ids: set[str]) -> dict:
    """Normalise one neutral film dict into DB fields + validated tags.

    Pure (no IO). Tag items are `"tag_id"` or `{"tag_id","confidence"}`; unknown
    tag_ids (not in the taxonomy) are dropped and reported in `dropped_tags`.
    """
    titles = raw["titles"]
    parsed_tags: list[tuple[str, float]] = []
    dropped: list[str] = []
    for t in raw.get("tags") or []:
        if isinstance(t, str):
            tid, conf = t, 1.0
        else:
            tid, conf = t["tag_id"], float(t.get("confidence", 1.0))
        if tid in valid_tag_ids:
            parsed_tags.append((tid, conf))
        else:
            dropped.append(tid)
    country = raw.get("country") or []
    return {
        "film_id": raw["id"],
        "title_zh": titles.get("zh") or primary_title(titles),
        "title_en": titles.get("en"),
        "description": raw.get("description", "") or "",
        "catchplay_url": raw.get("url"),
        "poster_url": raw.get("poster"),
        "release_year": raw.get("year"),
        "country_codes": ",".join(country) or None,
        "tmdb_director": raw.get("director"),
        "tmdb_cast": raw.get("cast"),
        "tags": parsed_tags,
        "dropped_tags": dropped,
    }


def _validate_doc(doc: dict) -> list[dict]:
    """Minimal runtime validation (full schema check is the check-jsonschema gate)."""
    if doc.get("version") != 1:
        sys.exit(f"unsupported seed version: {doc.get('version')!r} (expected 1)")
    films = doc.get("films")
    if not isinstance(films, list):
        sys.exit("seed file: 'films' must be a list")
    for f in films:
        if not f.get("id") or not f.get("titles"):
            sys.exit(f"seed file: each film needs id + titles (offending: {f!r})")
    return films


def _auto_tag(film_row: dict, valid_tag_ids: set[str]) -> list[tuple[str, float]]:
    """LLM-fill tags for a film with none (opt-in; needs a ready LLM backend)."""
    import asyncio

    from backend.services.auto_tag import AutoTagService

    result = asyncio.run(AutoTagService().execute({"film": film_row}))
    out = []
    for s in result.get("suggestions", []):
        tid = s["tag_id"] if isinstance(s, dict) else s.tag_id
        conf = (s.get("confidence", 1.0) if isinstance(s, dict) else s.confidence) or 1.0
        if tid in valid_tag_ids:
            out.append((tid, float(conf)))
    return out


def seed(path: Path, *, auto_tag: bool = False, compute_similar: bool = False) -> int:
    """Seed SQLite + Qdrant from a neutral seed file. Returns films seeded."""
    from backend import vector_store as vs
    from backend.services.embedder import EmbedService

    doc = json.loads(path.read_text(encoding="utf-8"))
    films = _validate_doc(doc)

    registry = TagRegistry()
    valid_ids = registry.all_tag_ids

    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    # Taxonomy first (film_tags FK references tags).
    for row in registry.to_db_rows():
        insert_tag(conn, **row)

    embed = EmbedService()
    client = vs.get_qdrant_client()
    vs.ensure_collection(client)

    for raw in films:
        f = parse_film(raw, valid_ids)
        tags, dropped = f.pop("tags"), f.pop("dropped_tags")
        insert_film(conn, **f)
        if dropped:
            print(f"  ! {f['film_id']}: dropped unknown tags {dropped}", flush=True)
        if not tags and auto_tag:
            tags = _auto_tag({**f, "title_zh": f["title_zh"]}, valid_ids)
        if not tags:
            print(f"  ~ {f['film_id']}: no tags (description-only embed)", flush=True)
        for tid, conf in tags:
            insert_film_tag(conn, f["film_id"], tid, confidence=conf, source="seed")
        conn.commit()

        # Embed + index.
        tag_ids = [tid for tid, _ in tags]
        text = EmbedService.build_film_text({**f, "tag_labels": tag_ids})
        vec = embed.embed_single(text)
        payload_tags = [
            {"tag_id": tid, "dimension": (registry.get_tag(tid) or {}).get("dimension", "unknown")}
            for tid in tag_ids
        ]
        vs.upsert_film_vector(client, f["film_id"], vec, vs.build_film_payload(f, payload_tags))

    conn.close()
    if compute_similar:
        import subprocess

        subprocess.run([sys.executable, "-m", "scripts.05_compute_similar"], check=True)

    print(
        f"seeded {len(films)} films into "
        f"db={settings.db_path} collection={settings.qdrant_collection}"
    )
    return len(films)


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed from a neutral films.seed.json")
    ap.add_argument("file", nargs="?", default="data/films.seed.json")
    ap.add_argument("--auto-tag", action="store_true", help="LLM-fill tags for films with none")
    ap.add_argument("--compute-similar", action="store_true", help="precompute similar films")
    args = ap.parse_args()
    seed(Path(args.file), auto_tag=args.auto_tag, compute_similar=args.compute_similar)


if __name__ == "__main__":
    main()
