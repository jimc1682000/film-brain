#!/usr/bin/env bash
# Post-deploy smoke check — liveness/readiness only, NOT ranking quality.
# Verifies the service is up and the core endpoints answer with a sane shape.
# A degraded search result (LLM breaker open → BM25) still passes; that is fine.
#
# Usage:
#   scripts/smoke_check.sh [BASE_URL]
# Default BASE_URL: http://localhost:8000
# Exits non-zero on the first failed check (suitable for gating a deploy).
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

echo "smoke: $BASE_URL"

# 1) Liveness — /health must be 200 and report status ok.
body="$(curl -fsS "$BASE_URL/health")" || fail "/health not 200"
case "$body" in
  *'"status"'*'"ok"'*) ;;
  *) fail "/health unexpected body: $body" ;;
esac
echo "  /health ok"

# 2) LLM health — must answer 200 (the path/breaker state itself is not asserted).
curl -fsS "$BASE_URL/api/llm-health" >/dev/null || fail "/api/llm-health not 200"
echo "  /api/llm-health ok"

# 3) Search readiness — POST must be 200 and return a results array. min_confidence
#    is floored so an empty/degraded library still yields a well-formed response.
body="$(curl -fsS -X POST "$BASE_URL/api/search/" \
  -H 'Content-Type: application/json' \
  -d '{"query":"smoke test","top_k":1,"min_confidence":0.0}')" || fail "/api/search/ not 200"
case "$body" in
  *'"results"'*) ;;
  *) fail "/api/search/ missing results key: $body" ;;
esac
echo "  /api/search/ ok"

echo "SMOKE PASS"
