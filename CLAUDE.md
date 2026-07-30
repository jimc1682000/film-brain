# AI Film Library Brain — CATCHPLAY+ Hackathon 2026

## Project Overview

AI-powered film tagging and semantic search prototype for CATCHPLAY+.
Converts 553 tree-based genres into 14-dimension, 395-tag flexible taxonomy.

## Tech Stack

- **Backend**: FastAPI (Python 3.11) → `backend/`
- **Frontend**: NiceGUI → `frontend/`
- **Database**: SQLite → `data/film_library.db`
- **Vector DB**: Qdrant (Docker, port 6333)
- **Embedding**: BAAI/bge-m3 (local, 1024 dims)
- **LLM** (runtime, optional — keyless-capable): split by task. **Query expansion** runs on a local Ollama model by default (frequent, cheap, no quota). **Auto-tag / re-analyze** prefers an optional cloud backend (`tagging_cloud_backend`, e.g. `openrouter` / `gemini`) guarded by a circuit breaker; on cloud failure or no key it falls back to the local model. `GET /api/llm-health` shows the live tagging path + breaker state. Both paths degrade gracefully — no key, no crash.

## Quick Start

```bash
# Fully containerized, keyless: qdrant + ollama (auto-pulls bge-m3) + backend
# + frontend. No host Python, no API key.
docker compose up -d

# Seed data — bring-your-own-films, no API key needed (bundled mock dataset).
# Any file matching data/films.seed.schema.json works; --auto-tag (LLM) optional.
# Award nominations from data/awards.seed.json are ingested after films (matched
# by title → in-library badges on /awards); override --awards FILE, disable ''.
docker compose exec backend python -m scripts.seed_from_file data/films.seed.json

# Or pull pre-built images from GHCR instead of building locally:
#   docker compose -f docker-compose.ghcr.yml up -d

# Run tests (host)
python -m pytest backend/tests/ -v
```

## API Docs

http://localhost:8000/api/docs (Swagger UI auto-generated; OpenAPI JSON at `/api/openapi.json`)

## Key Commands

- `python -m scripts.seed_from_file data/films.seed.json` — Run data pipeline
  (DB schema + taxonomy + films + awards + embeddings; `--auto-tag` LLM-fills
  missing tags, `--compute-similar` precomputes similar films)
- `python -m scripts.05_compute_similar` — Recompute similar-films table
  (canonical wrapper: `make recompute-similar`)
- `python -m pytest backend/tests/ -v` — Run all tests
- `make up` — Start Docker containers (qdrant + ollama + backend + frontend)
- `make check` — ruff lint + format check
- `make typecheck` — pyright (backend source)
- `make cov` — tests + per-module ≥80% coverage gate
- `make audit` — pip-audit dependency CVE scan
- `make mutation` — mutation testing (SLOW, on-demand only)
- `make e2e` — full-stack smoke (real Qdrant + embedder, local only)
- `make e2e-ui` — browser smoke (real Chromium vs live UI on mock data, SQLite-only, local only) — catches render/CSS/SVG regressions the in-process suite can't

## Invariants & Agent Contract

Hold these across any change; they encode decisions that are expensive to
rediscover. Verify before declaring done.

**Definition of done** (every change): `make check` + `make typecheck` clean,
`make cov` green (total ≥80% AND every module ≥80%), CI green. Don't claim
"done + verified" until CI conclusion is `success` — the suite passing locally
is necessary, not sufficient (e.g. CI lacks the local seeded DB).

**Architectural invariants:**

- **Boundaries go through Protocols** (ADR 0021): embedder / vector store /
  reranker / LLM are consumed via the `backend/interfaces.py` Protocols and
  resolved through `get_*` providers. Inject a fake (param / FastAPI
  `Depends` / constructor); do NOT monkeypatch the module function name.
- **Honest scoring is sacred** (ADR 0009): the three cosine confidence tiers
  (high/mid/low) cap the displayed score (95/68/42-ish). Never let a relative
  top-1 masquerade as 100%. CE absolute scores order only, never gauge truth.
- **Runs on synthetic data**: the system must work on the mock dataset
  (`backend/tests/fixtures/mock_films.py`, ADR 0020) — no real CATCHPLAY
  catalog required. Tests seed mock into a temp DB; never depend on an ambient
  `data/film_library.db` (that was the CI-red bug — see git log).
- **Degrade, don't crash**: an absent/partial `data/search-config.json` falls
  back to `search_config._DEFAULTS` (must stay complete). LLM failure degrades
  query expansion to BM25 + vector, never blocks search.

**Search ranking is the real behavioral contract.** The mocked test suite
verifies *plumbing*, not ranking *quality*. A green suite does NOT prove
ndcg@5 held — run `scripts/eval_search.py` and compare against
`docs/reports/eval-baseline-pre-refactor.json` before claiming ranking is
unchanged. (No automated eval gate yet — it's the next harness gap.)

**Public-readiness / safety:** no secrets, internal hosts, work emails, or real
catalog data in commits (betterleaks + pip-audit gate this). Irreversible or
outward actions (pushing a new public repo, deploying) need explicit human
confirmation — don't self-authorize.

**Tooling stays in sync:** ruff version in `.pre-commit-config.yaml` ==
CI pin (`.github/workflows/ci.yml`). Bump together.

**Commit messages:** Conventional Commits, gitmoji prefix optional/encouraged
(`cz_gitmoji`), enforced by the commitizen commit-msg hook — both `✨ feat(search): …`
and plain `feat(search): …` pass; a non-conventional message fails. Run
`pre-commit install` once per clone to wire the commit-msg hook
(`default_install_hook_types` covers it).

## Architecture

- `backend/services/` — Service layer (AutoTag, Embed, Search, Enrich, Feedback)
- `backend/routers/` — FastAPI routers
- `backend/db.py` — SQLite schema + CRUD
- `backend/vector_store.py` — Qdrant integration
- `backend/tag_registry.py` — Tag taxonomy manager
- `data/dimension-mapping.json` — 14-dimension tag taxonomy v1.4
