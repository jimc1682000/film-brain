"""Ingest award nominees from a JSON file.

Schema:
{
  "org_id": "oscars",
  "year": 2026,
  "source_url": "...",
  "ceremony_date": "YYYY-MM-DD",
  "nominees": [
    {"category": "...", "film_title_primary": "...", "film_title_alt": null,
     "person": null, "result": "won|nominated"}
  ]
}
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.award_manager import get_org, record_nomination
from backend.db import get_db


def ingest(path: str) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    org = get_org(data["org_id"])
    year = int(data["year"])
    source_url = data.get("source_url")
    ceremony_date = data.get("ceremony_date")
    nominees = data["nominees"]

    print(f"=== {org['name_en']} {year}: {len(nominees)} nominees ===")
    matched = 0
    with get_db() as conn:
        for i, nom in enumerate(nominees, 1):
            out = record_nomination(
                conn,
                org=org,
                year=year,
                category=nom["category"],
                primary_title=nom["film_title_primary"],
                alt_title=nom.get("film_title_alt"),
                person=nom.get("person"),
                result=nom.get("result", "nominated"),
                source_url=source_url,
                ceremony_date=ceremony_date,
            )
            if out["matched_film_id"]:
                matched += 1
            if i % 10 == 0 or i == len(nominees):
                print(f"  [{i}/{len(nominees)}] matched so far: {matched}")
    print(f"=== Done: matched={matched}/{len(nominees)} ===")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m scripts.ingest_awards_json <file.json> [...]")
        sys.exit(1)
    for path in sys.argv[1:]:
        ingest(path)


if __name__ == "__main__":
    main()
