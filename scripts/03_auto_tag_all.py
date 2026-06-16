"""Batch auto-tag all films using LLM (Sonnet + Opus escalation)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.db import get_db, get_film_tags, insert_film_tag
from backend.services.auto_tag import AutoTagService


async def main():
    # AutoTagService picks the backend from settings.llm_backend; require the
    # matching API key. Production uses Gemini, anthropic is a fallback.
    if settings.llm_backend == "gemini" and not settings.gemini_api_key:
        print("ERROR: llm_backend=gemini but GEMINI_API_KEY not set in .env")
        sys.exit(1)
    if settings.llm_backend == "anthropic" and not settings.anthropic_api_key:
        print("ERROR: llm_backend=anthropic but ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    service = AutoTagService()

    with get_db() as conn:
        films = conn.execute("SELECT * FROM films ORDER BY title_zh").fetchall()
        films = [dict(f) for f in films]

    print(f"=== Auto-Tag: {len(films)} films ===")
    tagged = 0
    skipped = 0
    errors = 0

    for i, film in enumerate(films):
        film_id = film["film_id"]

        # Skip if already has AI tags
        with get_db() as conn:
            existing = get_film_tags(conn, film_id)
            ai_tags = [t for t in existing if t["source"] == "ai"]
            if ai_tags:
                skipped += 1
                continue

        print(f"\n[{i + 1}/{len(films)}] {film['title_zh']}...")

        try:
            result = await service.execute({"film": film})
            suggestions = result.get("suggestions", [])

            # Persist to DB
            with get_db() as conn:
                for s in suggestions:
                    if isinstance(s, dict):
                        tag_id = s["tag_id"]
                        confidence = s["confidence"]
                    else:
                        tag_id = s.tag_id
                        confidence = s.confidence
                    insert_film_tag(conn, film_id, tag_id, confidence=confidence, source="ai")

            model = result.get("model_used", "unknown")
            escalated = " [ESCALATED]" if result.get("escalated") else ""
            print(f"  → {len(suggestions)} tags ({model}){escalated}")
            tagged += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

    print(f"\n=== Done: {tagged} tagged, {skipped} skipped, {errors} errors ===")


if __name__ == "__main__":
    asyncio.run(main())
