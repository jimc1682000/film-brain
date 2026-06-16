"""Add Vero-requested fine-grained tags into data/dimension-mapping.json
and persist them into the live SQLite tags table so /save validation lets
them through.

Tags added:
  emotion: toxic-romance, infidelity-consequence, hardcore
  theme:   family-drama, family-comedy

Run once: uv run python -m scripts.expand_taxonomy
Idempotent: re-running with the same payload is a no-op.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as `python -m scripts.expand_taxonomy` from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_db, insert_tag  # noqa: E402

NEW_TAGS: list[dict] = [
    {
        "dimension": "emotion",
        "tag_id": "toxic-romance",
        "labels": {"en": "Toxic Romance", "zh_TW": "虐戀", "in_ID": "Cinta Beracun"},
    },
    {
        "dimension": "emotion",
        "tag_id": "infidelity-consequence",
        "labels": {
            "en": "Aftermath of Infidelity",
            "zh_TW": "出軌後果",
            "in_ID": "Konsekuensi Perselingkuhan",
        },
    },
    {
        "dimension": "emotion",
        "tag_id": "hardcore",
        "labels": {"en": "Hardcore", "zh_TW": "硬派", "in_ID": "Hardcore"},
    },
    {
        "dimension": "theme",
        "tag_id": "family-drama",
        "labels": {"en": "Family Drama", "zh_TW": "家庭劇情", "in_ID": "Drama Keluarga"},
    },
    {
        "dimension": "theme",
        "tag_id": "family-comedy",
        "labels": {"en": "Family Comedy", "zh_TW": "家庭喜劇", "in_ID": "Komedi Keluarga"},
    },
]


def patch_taxonomy(path: Path = Path("data/dimension-mapping.json")) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    added = 0
    for new in NEW_TAGS:
        dim = data["dimensions"][new["dimension"]]
        existing_ids = {t["tag_id"] for t in dim.get("tags", [])}
        if new["tag_id"] in existing_ids:
            continue
        dim.setdefault("tags", []).append({"tag_id": new["tag_id"], "labels": new["labels"]})
        added += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {"added": added, "total": len(NEW_TAGS)}


def patch_db() -> dict:
    added = 0
    with get_db() as conn:
        for t in NEW_TAGS:
            row = conn.execute("SELECT 1 FROM tags WHERE tag_id = ?", (t["tag_id"],)).fetchone()
            if row:
                continue
            insert_tag(
                conn,
                tag_id=t["tag_id"],
                dimension=t["dimension"],
                label_en=t["labels"]["en"],
                label_zh_tw=t["labels"]["zh_TW"],
                label_in_id=t["labels"]["in_ID"],
            )
            added += 1
    return {"db_added": added}


def main() -> int:
    print(">>> Patching data/dimension-mapping.json")
    print("    ", patch_taxonomy())
    print(">>> Patching DB tags table")
    print("    ", patch_db())
    return 0


if __name__ == "__main__":
    sys.exit(main())
