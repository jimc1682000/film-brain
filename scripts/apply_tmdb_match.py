"""Apply a human/LLM-adjudicated TMDb match to a film row.

The validated matcher in 02_enrich_tmdb refuses candidates it can't
corroborate (localized CP titles, franchise aliases, language-version
suffixes). When Claude Code has adjudicated the right TMDb entry by
reading the CP description, this script does the mechanical write:
fetch details, update the films row, refresh the enrich cache.

Usage:
    python -m scripts.apply_tmdb_match <film_id> <tmdb_id> <movie|tv>
    python -m scripts.apply_tmdb_match <film_id> --null     # confirmed not on TMDb

Used by the `tmdb-rescue` skill. Judgment lives in the skill (Claude),
not here — this script trusts its arguments.
"""

import importlib
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.db import get_db

enrich = importlib.import_module("scripts.02_enrich_tmdb")


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    film_id = sys.argv[1]

    with get_db() as conn:
        row = conn.execute(
            "SELECT title_zh, tmdb_id FROM films WHERE film_id=?", (film_id,)
        ).fetchone()
        if not row:
            print(f"ERROR: film_id {film_id} not found")
            sys.exit(1)

        cache = settings.tmdb_cache_dir / f"{film_id}.json"

        if sys.argv[2] == "--null":
            conn.execute(
                "UPDATE films SET tmdb_id=NULL, tmdb_overview=NULL, tmdb_genres=NULL, "
                "tmdb_keywords=NULL, tmdb_vote_avg=NULL, tmdb_cast=NULL, "
                "tmdb_director=NULL WHERE film_id=?",
                (film_id,),
            )
            conn.commit()
            if cache.exists():
                cache.unlink()
            print(f"NULLED {row['title_zh']} (confirmed not on TMDb)")
            return

        tmdb_id = int(sys.argv[2])
        media_type = sys.argv[3] if len(sys.argv) > 3 else "movie"
        if media_type not in ("movie", "tv"):
            print("ERROR: media_type must be movie or tv")
            sys.exit(1)

        with httpx.Client(timeout=15) as client:
            details = enrich.get_details(client, tmdb_id, media_type)
        data = enrich.extract_enrichment(details)
        conn.execute(
            "UPDATE films SET tmdb_id=?, tmdb_overview=?, tmdb_genres=?, "
            "tmdb_keywords=?, tmdb_vote_avg=?, tmdb_cast=?, tmdb_director=? "
            "WHERE film_id=?",
            (
                data["tmdb_id"],
                data["tmdb_overview"],
                data["tmdb_genres"],
                data["tmdb_keywords"],
                data["tmdb_vote_avg"],
                data["tmdb_cast"],
                data["tmdb_director"],
                film_id,
            ),
        )
        conn.commit()
        # Mark as human/LLM-adjudicated so verify_tmdb_blind doesn't re-flag
        # alias matches it can't corroborate mechanically.
        data["adjudicated"] = True
        cache.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        title = details.get("title") or details.get("name")
        print(
            f"APPLIED {row['title_zh']} → {media_type} #{tmdb_id} ({title}) "
            f"overview={'yes' if data['tmdb_overview'] else 'EMPTY'}"
        )


if __name__ == "__main__":
    main()
