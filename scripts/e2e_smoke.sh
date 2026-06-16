#!/usr/bin/env bash
# Full-stack e2e smoke test — real Qdrant + real embedder + live FastAPI server,
# driven over HTTP against the synthetic mock dataset (no CATCHPLAY data needed).
#
# This is the "真 server + 真 Qdrant" counterpart to the in-process TestClient
# e2e suite. It is LOCAL-ONLY (needs Docker + an embedding backend) — CI runs the
# mocked pytest suite instead (.github/workflows/ci.yml).
#
# Prereqs:
#   - docker (for Qdrant)
#   - an embedding backend: either Ollama with `ollama pull bge-m3`
#     (EMBEDDING_BACKEND=ollama, default) or sentence-transformers installed
#     (EMBEDDING_BACKEND=sentence-transformers)
#   - python deps installed (pip install -r requirements.txt)
#
# Usage:  bash scripts/e2e_smoke.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${E2E_PORT:-8099}"
TMP="$(mktemp -d)"
export DB_PATH="$TMP/e2e.db"
export QDRANT_COLLECTION="e2e_smoke"
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

cleanup() {
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  docker compose stop qdrant 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

echo "==> prereq checks"
command -v docker >/dev/null || { echo "FAIL: docker not found"; exit 1; }
"$PY" -c "import fastapi, qdrant_client" 2>/dev/null || { echo "FAIL: python deps missing (pip install -r requirements.txt)"; exit 1; }

echo "==> starting Qdrant"
docker compose up -d qdrant
for _ in $(seq 1 30); do
  curl -sf "http://localhost:${QDRANT_PORT:-6333}/readyz" >/dev/null 2>&1 && break
  sleep 1
done

echo "==> seeding synthetic mock data + embeddings into Qdrant"
"$PY" - <<'PY'
import sqlite3
from backend.config import settings
from backend.db import init_db
from backend.tests.fixtures.mock_films import MOCK_FILMS, seed_mock_db
from backend.services.embedder import EmbedService
from backend import vector_store as vs

init_db(settings.db_path)
conn = sqlite3.connect(str(settings.db_path)); conn.row_factory = sqlite3.Row
seed_mock_db(conn)

embed = EmbedService()
client = vs.get_qdrant_client()
vs.ensure_collection(client)
for film in MOCK_FILMS:
    row = conn.execute("SELECT * FROM films WHERE film_id=?", (film["film_id"],)).fetchone()
    tags = [{"tag_id": t} for t in film["tags"]]
    text = EmbedService.build_film_text({**dict(row), "tag_labels": film["tags"]})
    vec = embed.embed_single(text)
    vs.upsert_film_vector(client, film["film_id"], vec, vs.build_film_payload(dict(row), tags))
print(f"seeded {len(MOCK_FILMS)} films")
PY

echo "==> starting backend on :$PORT"
"$PY" -m uvicorn backend.main:app --port "$PORT" --log-level warning &
SERVER_PID=$!
for _ in $(seq 1 40); do
  curl -sf "http://localhost:$PORT/docs" >/dev/null 2>&1 && break
  sleep 1
done

echo "==> smoke: POST /api/search"
RESP="$(curl -sf -X POST "http://localhost:$PORT/api/search/" \
  -H 'Content-Type: application/json' \
  -d '{"query":"好笑的喜劇","top_k":5}')"
# Pass RESP via env, not a pipe: the heredoc already owns stdin (it IS the
# script for `python -`), so a piped stdin would be discarded (SC2259).
RESP="$RESP" "$PY" - <<'PY'
import json
import os

r = json.loads(os.environ["RESP"])
assert "results" in r, f"no results key: {r}"
assert r.get("total", 0) >= 1, f"expected >=1 result, got {r.get('total')}"
print(f"OK: {r['total']} results, top = {r['results'][0]['title_zh']}")
PY

echo "==> e2e smoke PASSED"
