#!/usr/bin/env bash
# Browser e2e-ui smoke — real Chromium against the live frontend + backend, on
# the synthetic mock dataset. Catches render/CSS/SVG regressions the in-process
# pytest suite cannot see (e.g. the title-card poster clipped out of the bubble
# layout). LOCAL-ONLY / opt-in — run via `make e2e-ui`; CI runs the mocked suite.
#
# SQLite-only by design: every page these tests visit is DB-backed (no vector
# search on load), so no Qdrant, no Ollama, no Docker. The backend boots fine
# with Qdrant unreachable (it degrades; search just isn't exercised here).
#
# Prereqs (the Makefile target installs them):
#   - app deps (pip install -r requirements.txt) in $PYTHON
#   - playwright + pytest-playwright (requirements-e2e.txt) + `playwright install chromium`
#
# Usage:  make e2e-ui   (or: bash scripts/e2e_ui.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BE_PORT="${E2E_UI_BE_PORT:-8097}"
FE_PORT="${E2E_UI_FE_PORT:-8087}"
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

TMP="$(mktemp -d)"
export DB_PATH="$TMP/e2e_ui.db"
export QDRANT_COLLECTION="e2e_ui"
# Point Qdrant at a dead port: pages here never search, and the backend degrades
# gracefully — this just guarantees we never touch a real/again-shared instance.
export QDRANT_HOST="127.0.0.1"
export QDRANT_PORT="6399"
export E2E_UI_BASE="http://localhost:$FE_PORT"
export E2E_UI_ARTIFACTS="${E2E_UI_ARTIFACTS:-$ROOT/.e2e-ui-artifacts}"

cleanup() {
  [ -n "${FE_PID:-}" ] && kill "$FE_PID" 2>/dev/null || true
  [ -n "${BE_PID:-}" ] && kill "$BE_PID" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

echo "==> prereq checks"
"$PY" -c "import fastapi, nicegui, playwright" 2>/dev/null || {
  echo "FAIL: deps missing — pip install -r requirements.txt -r requirements-e2e.txt"; exit 1; }

echo "==> seeding synthetic mock films + awards into $DB_PATH (SQLite only)"
"$PY" - <<'PY'
import sqlite3
from pathlib import Path
from backend.config import settings
from backend.db import init_db
from backend.tests.fixtures.mock_films import seed_mock_db
from scripts.seed_from_file import seed_awards

init_db(settings.db_path)
conn = sqlite3.connect(str(settings.db_path)); conn.row_factory = sqlite3.Row
seed_mock_db(conn)
print("  seeded", conn.execute("SELECT count(*) FROM films").fetchone()[0], "films")
# Award nominees (titles match the mock films → in-library badges). Same seed
# file fresh `make seed` uses — no inline duplication.
seed_awards(conn, Path("data/awards.seed.json"))
PY

echo "==> starting backend on :$BE_PORT"
"$PY" -m uvicorn backend.main:app --port "$BE_PORT" --log-level warning &
BE_PID=$!
for _ in $(seq 1 40); do
  curl -sf "http://localhost:$BE_PORT/health" >/dev/null 2>&1 && break; sleep 1
done

echo "==> starting frontend on :$FE_PORT (BACKEND_URL=:$BE_PORT)"
BACKEND_URL="http://localhost:$BE_PORT" FRONTEND_PORT="$FE_PORT" "$PY" -m frontend.app \
  > "$TMP/frontend.log" 2>&1 &
FE_PID=$!
for _ in $(seq 1 40); do
  curl -sf "http://localhost:$FE_PORT/" >/dev/null 2>&1 && break; sleep 1
done

echo "==> running browser tests (artifacts → $E2E_UI_ARTIFACTS)"
"$PY" -m pytest tests/e2e_ui -v
