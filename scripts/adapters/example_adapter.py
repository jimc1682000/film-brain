"""Example source adapter → films.seed.json.

The system is brand-neutral: `scripts/seed_from_file.py` loads any dataset
matching `data/films.seed.schema.json`. An *adapter* is the small, replaceable
piece that turns YOUR source (a CSV export, a CMS API, a scrape, a catalog dump)
into that neutral format. Loader + format are public; adapters are yours.

This template shows the contract with a tiny inline source. Copy it, swap
`fetch_source()` for your real source, keep the mapping shape, and pipe the
output through the loader:

    python -m scripts.adapters.example_adapter > data/films.seed.json
    python -m scripts.seed_from_file data/films.seed.json

(The private CATCHPLAY adapter is exactly this with `fetch_source()` reading the
catalogue — it lives outside the public repo and emits the same format.)
"""

from __future__ import annotations

import json
import sys


def fetch_source() -> list[dict]:
    """Return raw rows from YOUR source. Replace this body.

    Here: two hand-written rows standing in for whatever you pull (DB/API/file).
    """
    return [
        {
            "key": "src-42",
            "name_zh": "範例電影",
            "name_en": "Example Film",
            "synopsis": "一段示範用的劇情描述。",
            "genres": ["drama"],  # must be taxonomy tag_ids (data/dimension-mapping.json)
            "poster": None,
            "release": 2024,
            "countries": ["TW"],
        }
    ]


def to_seed_film(row: dict) -> dict:
    """Map ONE source row to a neutral seed film (films.seed.schema.json).

    Required: id, titles (>=1). Everything else optional. Tags absent/empty →
    seed_from_file --auto-tag can LLM-fill them, else the film seeds tag-less.
    """
    titles = {}
    if row.get("name_zh"):
        titles["zh"] = row["name_zh"]
    if row.get("name_en"):
        titles["en"] = row["name_en"]
    film: dict = {"id": row["key"], "titles": titles}
    if row.get("synopsis"):
        film["description"] = row["synopsis"]
    if row.get("genres"):
        film["tags"] = row["genres"]  # or [{"tag_id": "...", "confidence": 0.8}, ...]
    if row.get("poster"):
        film["poster"] = row["poster"]
    if row.get("release"):
        film["year"] = row["release"]
    if row.get("countries"):
        film["country"] = row["countries"]  # ISO 3166-1 alpha-2
    return film


def main() -> None:
    doc = {"version": 1, "films": [to_seed_film(r) for r in fetch_source()]}
    json.dump(doc, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
