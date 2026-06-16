"""Generate embeddings for all films and upsert into Qdrant."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_db, get_film_tags
from backend.services.embedder import EmbedService
from backend.vector_store import (
    build_film_payload,
    ensure_collection,
    get_qdrant_client,
    upsert_film_vector,
)


def main():
    print("=== Step 1: Init embedding model ===")
    embed_service = EmbedService()

    print("=== Step 2: Connect to Qdrant ===")
    client = get_qdrant_client()
    ensure_collection(client)

    print("=== Step 3: Load films ===")
    with get_db() as conn:
        films = conn.execute("SELECT * FROM films ORDER BY title_zh").fetchall()
        films = [dict(f) for f in films]

    print(f"=== Step 4: Generate embeddings for {len(films)} films ===")

    for i, film in enumerate(films):
        film_id = film["film_id"]

        # Get tags for this film
        with get_db() as conn:
            tag_rows = get_film_tags(conn, film_id)

        # Add tag labels to film for embedding
        film["tag_labels"] = [f"{t['label_zh_tw']}({t['label_en']})" for t in tag_rows]

        # Build composite text and embed
        text = EmbedService.build_film_text(film)
        vector = embed_service.embed_single(text)

        # Build payload and upsert
        payload = build_film_payload(film, tag_rows)
        upsert_film_vector(client, film_id, vector, payload)

        print(f"  [{i + 1}/{len(films)}] {film['title_zh']} ({len(vector)}d)")

    print(f"\n=== Done: {len(films)} films embedded and indexed ===")


if __name__ == "__main__":
    main()
