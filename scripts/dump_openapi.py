"""Dump the FastAPI OpenAPI schema to site/docs/public/openapi.json.

The committed JSON is what the docs site renders (Redoc). A CI drift gate
regenerates this and fails if it's stale, so the published API reference can't
silently fall behind the code. Run:  python -m scripts.dump_openapi
"""

import json
from pathlib import Path

from backend.main import app

OUT = Path("site/docs/public/openapi.json")


def main() -> None:
    schema = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
