#!/usr/bin/env bash
# One-command rollback to a previously published image tag.
#
# The release pipeline (.github/workflows/image.yml) publishes backend/frontend
# images to GHCR per git tag and as :master. NOTE the image tag has NO leading
# "v": docker/metadata-action `type=semver,pattern={{version}}` turns git tag
# `v0.1.0` into image tag `0.1.0`. This pins the compose stack to a given image
# tag, restarts, and runs the post-deploy smoke check; on failure it reports
# loudly so you can re-run with a known-good tag.
#
# Usage:
#   scripts/rollback.sh <image-tag> [BASE_URL]
# Example (image tag, not the git tag — no leading v):
#   scripts/rollback.sh 0.1.0
#
# Find available tags on GHCR (packages film-brain-backend / -frontend).
set -euo pipefail

TAG="${1:-}"
BASE_URL="${2:-http://localhost:8000}"
COMPOSE_FILE="docker-compose.ghcr.yml"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "$TAG" ]]; then
  echo "usage: scripts/rollback.sh <image-tag> [BASE_URL]" >&2
  exit 2
fi

cd "$HERE"
echo "rollback: pinning images to tag '$TAG'"

IMAGE_TAG="$TAG" docker compose -f "$COMPOSE_FILE" pull backend frontend
IMAGE_TAG="$TAG" docker compose -f "$COMPOSE_FILE" up -d

echo "rollback: waiting for the backend to answer…"
for _ in $(seq 1 30); do
  if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then break; fi
  sleep 2
done

if scripts/smoke_check.sh "$BASE_URL"; then
  echo "ROLLBACK OK — now running tag '$TAG'"
else
  echo "ROLLBACK SMOKE FAILED on tag '$TAG' — try another known-good tag" >&2
  exit 1
fi
