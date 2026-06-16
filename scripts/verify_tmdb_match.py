"""Verify films.tmdb_id correctness by comparing CP description vs tmdb_overview.

The title-fuzzy matcher in 02_enrich_tmdb can pick the wrong film (e.g.
正義兄弟會 → Room). When that happens the CATCHPLAY description and the TMDb
overview describe different movies, so their embedding similarity collapses.

Screen: embed both texts (bge-m3), cosine ascending — lowest = most suspect.
Read-only; emits a JSON report for review.

Usage:
    python -m scripts.verify_tmdb_match [--json docs/reports/tmdb-match-report.json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_db
from backend.services.embedder import EmbedService


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    with get_db() as conn:
        rows = conn.execute(
            "SELECT film_id, title_zh, title_en, tmdb_id, imdb_id, release_year, "
            "description, tmdb_overview FROM films WHERE tmdb_id IS NOT NULL"
        ).fetchall()

    both = [
        r for r in rows if (r["description"] or "").strip() and (r["tmdb_overview"] or "").strip()
    ]
    blind = [r for r in rows if not (r["tmdb_overview"] or "").strip()]
    no_desc = len(rows) - len(both) - len(blind)

    print(f"=== tmdb match screen: {len(rows)} films with tmdb_id ===")
    print(f"    comparable (desc+overview): {len(both)}")
    print(f"    BLIND (no tmdb_overview):   {len(blind)}  ← semantic screen can't see these")
    if no_desc:
        print(f"    no CP description:          {no_desc}")

    embedder = EmbedService()
    scored = []
    batch = 32
    for i in range(0, len(both), batch):
        chunk = both[i : i + batch]
        descs = embedder.embed([r["description"][:1500] for r in chunk])
        overs = embedder.embed([r["tmdb_overview"][:1500] for r in chunk])
        for r, d, o in zip(chunk, descs, overs, strict=True):
            scored.append(
                {
                    "film_id": r["film_id"],
                    "title_zh": r["title_zh"],
                    "title_en": r["title_en"],
                    "tmdb_id": r["tmdb_id"],
                    "imdb_id": r["imdb_id"],
                    "release_year": r["release_year"],
                    "cosine": round(cosine(d, o), 4),
                }
            )
        print(f"    embedded {min(i + batch, len(both))}/{len(both)}", flush=True)

    scored.sort(key=lambda s: s["cosine"])

    print("\n=== lowest-similarity (most suspect) ===")
    for s in scored[:30]:
        print(
            f"  {s['cosine']:.4f}  {s['title_zh']}  (tmdb {s['tmdb_id']}, imdb {s['imdb_id'] or '-'})"
        )

    report = {
        "total_with_tmdb_id": len(rows),
        "comparable": len(both),
        "blind_no_overview": [
            {
                "film_id": r["film_id"],
                "title_zh": r["title_zh"],
                "tmdb_id": r["tmdb_id"],
                "imdb_id": r["imdb_id"],
            }
            for r in blind
        ],
        "scored": scored,
    }
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\nReport → {args.json_path}")


if __name__ == "__main__":
    main()
