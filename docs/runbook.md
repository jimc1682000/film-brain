# Runbook

Operational guide for running and recovering the service. Host-agnostic — all
commands assume you are on the box that runs the containers and hit `localhost`.

## Architecture at a glance

Four containers (see `docker-compose.ghcr.yml`): `backend` (FastAPI),
`frontend` (NiceGUI), `qdrant` (vector store), `ollama` (local LLM/embeddings).
Backend images are published to GHCR by the release pipeline
(`.github/workflows/image.yml`) on a `v*` tag and as `:master`.

## Deploy

```bash
# Pull the published images and (re)start:
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d

# Tail logs:
docker compose -f docker-compose.ghcr.yml logs -f backend
```

Local build instead of GHCR: `make up` (uses `docker-compose.yml`).

> **Non-root containers + the `./data` bind mount.** The backend runs as uid
> 1000 (`appuser`). It writes the SQLite DB / caches into the bind-mounted
> `./data`, so that host directory must be writable by uid 1000 — it is when the
> repo was cloned by the host's first user (also uid 1000). If `./data` is owned
> by another uid, either `chown -R 1000:1000 ./data` or add `user: "<host-uid>"`
> to the backend service in the compose file.

## Access control (edge auth + rate limit, ADR 0025)

The app has no built-in user auth. Gate it at the edge:

```bash
# Hash a password, then run the optional Caddy edge (Basic Auth) in front:
BASIC_AUTH_HASH=$(docker run --rm caddy:2 caddy hash-password -p 'yourpass')
# standalone public demo — a hostname gets auto-HTTPS:
CADDY_SITE_ADDRESS=demo.example.com BASIC_AUTH_HASH="$BASIC_AUTH_HASH" \
  docker compose -f docker-compose.ghcr.yml -f docker-compose.caddy.yml up -d
```

Topology:

```
standalone:  Caddy (Basic Auth + auto-TLS) → frontend → backend
behind Traefik (VPS):  Traefik (TLS) → Caddy (Basic Auth, CADDY_SITE_ADDRESS=:80) → frontend → backend
                       Traefik routes only /api (search) + public paths to backend; admin paths stay internal
```

Search rate limit: off by default; enable in `data/search-config.json`
(`"rate_limit": {"enabled": true, "limit": 30, "window_seconds": 60}`). The
backend must run with `--proxy-headers` (the image already does) so per-IP limits
see the real client behind the proxy.

## Health checks

| Endpoint | Tells you |
| --- | --- |
| `GET /health` | Liveness + warmed tag-vector cache size. |
| `GET /api/llm-info` | Active LLM backend + model that actually runs. |
| `GET /api/llm-health` | Which path auto-tag takes now (cloud vs local) + circuit-breaker state. |

```bash
curl -fsS localhost:8000/health
curl -fsS localhost:8000/api/llm-health
```

## Failure modes and recovery

| Symptom | Likely cause | Recovery |
| --- | --- | --- |
| Search returns but tags/expansion look thin | LLM circuit breaker open (cloud failing) or no key | Expected degrade to BM25 + vector. Check `/api/llm-health` → `circuit.open`. It half-opens after the cooldown; no action needed unless persistent. If persistent, check the cloud key / quota. |
| `/health` slow right after start | Background warmup (tag cache, FTS rebuild, cross-encoder load) still running | Readiness is not blocked by warmup; first request needing an unwarmed piece loads it lazily. Wait a few minutes; watch startup logs. |
| Search 500s / Qdrant errors | Qdrant container down or collection missing | `docker compose ... restart qdrant`; re-seed if the collection is empty (`python -m scripts.seed_from_file …`). |
| All searches empty | DB not seeded | Seed the dataset (see CONTRIBUTING / Quick Start). |
| Config change not taking effect | `data/search-config.json` malformed | Tuning hot-reloads on the next search; a malformed/absent file falls back to built-in defaults (search never breaks). Fix the JSON. |

## Rollback

The release pipeline tags backend/frontend images per git tag and as `:master`.
The **image** tag has no leading `v` — `docker/metadata-action`
`type=semver,pattern={{version}}` turns git tag `v0.1.0` into image tag `0.1.0`.

Scripted (preferred) — pins the tag, restarts, and runs the smoke check:

```bash
scripts/rollback.sh 0.1.0          # image tag, NOT the git tag (no leading v)
```

Manual equivalent — the compose image tag is parametrized (`${IMAGE_TAG:-master}`):

```bash
IMAGE_TAG=0.1.0 docker compose -f docker-compose.ghcr.yml pull
IMAGE_TAG=0.1.0 docker compose -f docker-compose.ghcr.yml up -d
scripts/smoke_check.sh             # /health + /api/llm-health + a canned search
```

## Ownership

Single maintainer (see `CODEOWNERS`). No formal on-call. File issues for
anything operational.
