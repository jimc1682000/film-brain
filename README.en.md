[繁體中文](README.md) | **English**

# film-brain — AI Film Library Brain

Semantic film search + multi-dimension auto-tagging. Turns a flat catalogue into
a 14-dimension / ~400-tag taxonomy and lets people find films by *meaning*
("something to cry to", "a tense Korean thriller") instead of exact keywords.

Built for the CATCHPLAY+ Hackathon 2026; open-sourced as a runnable, brand-neutral core.

**Live demo / write-up:** https://jimc1682000.github.io/film-brain/

## What this repo is — vs the live demo

| | |
| --- | --- |
| **This repo (code)** | The **runnable, mock-based core** — FastAPI + NiceGUI + Qdrant + local bge-m3 + cross-encoder. **Keyless**: search runs on local models, no API key required. Bring your own films via a neutral seed format. |
| **The live demo (site)** | A portfolio **showcasing the full system run on the real CATCHPLAY+ catalogue** — the search replay, the eval-iteration story, the debugging case studies. |

So a couple of things the showcase describes are **not shipped here**, by design:
- **Catalogue ingest / scrapers** are a *private source adapter*. This repo ships the **generic loader** (`scripts/seed_from_file.py`) + a **neutral adapter template** (`scripts/adapters/example_adapter.py`) + a bundled **mock dataset** — bring your own films in the documented format (`data/films.seed.schema.json`).
- **The 45-query eval numbers** (nDCG@5 0.93 → 0.96 on the site) were measured on the *real catalogue*. The repo ships the **same harness** (`scripts/eval_search.py`) + the **same 45-query set** (`data/eval-queries.json` — query strings, LLM-judged at runtime, no gold labels), but the headline numbers only reproduce against the real catalogue; on the bundled mock films the same harness yields different scores.

The system is **not coupled to CATCHPLAY** — it runs on any dataset matching the seed schema.

## Quickstart (keyless, fully containerized)

Everything runs in containers — qdrant, the local **bge-m3** embedder (ollama,
auto-pulled on first `up`), backend, and frontend. No host Python, no API key.

```bash
docker compose up -d        # qdrant + ollama (pulls bge-m3) + backend + frontend
docker compose exec backend python -m scripts.seed_from_file data/films.seed.json
# open http://localhost:8080  (API docs: http://localhost:8000/api/docs)
```

First `up` downloads the bge-m3 weights (~1.2 GB) into a named volume; later
runs are instant. A cloud LLM key (OpenRouter / etc.) is **optional** — drop a
`.env` (see `.env.example`) to enrich query understanding + auto-tagging;
without it those degrade gracefully and search still works.

### Run from pre-built images (skip the local build)

`.github/workflows/image.yml` publishes `backend` / `frontend` images to GitHub
Container Registry on every push to master. Once the packages are **public**
(GitHub → repo → Packages → each package → *Package settings* → change
visibility to public), pull instead of build:

```bash
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml exec backend \
    python -m scripts.seed_from_file data/films.seed.json
```

## Bring your own films

Drop a `data/films.seed.json` matching `data/films.seed.schema.json` (titles map,
taxonomy tags, optional poster/year/country/cast), then `make seed`. Tags can be
LLM-filled with `--auto-tag`. See `scripts/adapters/example_adapter.py` for the
source-adapter contract.

## Architecture

Detailed C4 + UML diagrams under [`docs/architecture/`](docs/architecture/):
system context, containers, components, Protocol class diagram, the hybrid-search
pipeline + sequences, the data model (ERD), and deployment. Design decisions are
in [`docs/adr/`](docs/adr/).

## Tech

FastAPI · NiceGUI · SQLite · Qdrant · BAAI/bge-m3 (local) · bce-reranker cross-encoder ·
hybrid recall (vector + BM25/FTS5+jieba → RRF) · honest cosine-tier scoring · OpenRouter-free / local-Ollama LLM.

## License

MIT — see [LICENSE](LICENSE).
