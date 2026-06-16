"""Cross-check award_nominees rows against Wikidata.

Iterates films that have both an imdb_id AND existing award_nominees rows,
queries Wikidata for award statements via the SPARQL endpoint, and emits
per-row verdicts (verified / suspicious / unknown).

Usage:
  uv run python -m scripts.validate_awards_wikidata          # report
  uv run python -m scripts.validate_awards_wikidata --json out.json
  uv run python -m scripts.validate_awards_wikidata --film-id <fid>  # single film

Read-only by design. Acting on suspicious rows (delete? mark verified?) is a
separate decision left to the operator.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict

from backend.db import get_db
from backend.validators.award_validator import (
    query_film_awards,
    verify_nominee_row,
)


def collect_targets(conn, film_id: str | None) -> dict[str, list[dict]]:
    """Map imdb_id -> list of nominee rows for films we can verify."""
    sql = (
        "SELECT an.id, an.org_id, an.year, an.tag_id, an.matched_film_id, "
        "       f.imdb_id, f.title_zh "
        "FROM award_nominees an "
        "JOIN films f ON f.film_id = an.matched_film_id "
        "WHERE f.imdb_id IS NOT NULL AND f.imdb_id LIKE 'tt%'"
    )
    params: tuple = ()
    if film_id:
        sql += " AND an.matched_film_id = ?"
        params = (film_id,)
    rows = conn.execute(sql, params).fetchall()
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["imdb_id"]].append(dict(r))
    return groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="Write full result list to this path")
    parser.add_argument("--film-id", help="Restrict to one CATCHPLAY+ film_id")
    parser.add_argument(
        "--pause",
        type=float,
        default=1.0,
        help="Seconds to sleep between SPARQL calls (be polite)",
    )
    args = parser.parse_args(argv)

    with get_db() as conn:
        groups = collect_targets(conn, args.film_id)

    if not groups:
        print("No films with imdb_id + award_nominees rows to verify.")
        return 0

    print(
        f"Verifying {sum(len(v) for v in groups.values())} nominee rows "
        f"across {len(groups)} films...\n"
    )

    results: list[dict] = []
    counters: dict[str, int] = defaultdict(int)

    for imdb_id, rows in groups.items():
        title = rows[0]["title_zh"]
        wikidata_awards = query_film_awards(imdb_id)
        for row in rows:
            verdict = verify_nominee_row(row, wikidata_awards)
            counters[verdict["verdict"]] += 1
            results.append(verdict)
            sym = {"verified": "✓", "suspicious": "✗", "unknown": "?"}.get(verdict["verdict"], "?")
            print(
                f"  {sym} {title:20s} [{imdb_id}] org={verdict['org_id']:18s} "
                f"year={verdict['year']} -> {verdict['verdict']:11s} "
                f"{verdict['reason']}"
            )
        time.sleep(args.pause)

    print("\n--- summary ---")
    for k in ("verified", "suspicious", "unknown"):
        print(f"  {k:11s}: {counters[k]}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nFull report → {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
