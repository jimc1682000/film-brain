#!/usr/bin/env bash
# Validate the Caddyfile structurally via the caddy:2 image (no local binary).
# Dummy env satisfies the {$VAR} placeholders so validate checks the config shape
# without any real secret. BASIC_AUTH_HASH must be a valid bcrypt FORM (Caddy
# base64-decodes it), so we GENERATE a throwaway hash at runtime rather than
# hardcode one (keeps a bcrypt-looking string out of the repo / secret scanners).
# Used by the pre-commit caddy-validate hook and the CI docker job.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

dummy_hash="$(docker run --rm caddy:2 caddy hash-password --plaintext validate-only)"

docker run --rm \
  -e CADDY_SITE_ADDRESS=:80 \
  -e BASIC_AUTH_USER=admin \
  -e BASIC_AUTH_HASH="$dummy_hash" \
  -v "$PWD:/src:ro" -w /src caddy:2 \
  caddy validate --adapter caddyfile --config Caddyfile
