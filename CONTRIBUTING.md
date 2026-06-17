# Contributing

Thanks for your interest. This is a prototype, but it ships with real quality
gates — please keep them green.

## Dev setup

```bash
# Python 3.11+. Install dev deps:
pip install -e ".[dev]"

# Wire the commit-msg + pre-commit hooks (once per clone):
pre-commit install

# Seed the bundled mock dataset (no API key needed):
python -m scripts.seed_from_file data/films.seed.json

# Run the backend:
uvicorn backend.main:app --reload --port 8000   # http://localhost:8000/api/docs

# Run the frontend (separate terminal):
python -m frontend.app
```

Docker path: `make up` (backend + frontend + Qdrant), `make down`, `make logs`.

## Before you open a PR

Run the same gates CI enforces:

```bash
make check       # ruff lint + format check
make typecheck   # pyright (backend source)
make cov         # tests + coverage (total >=80%, every module >=80%)
make audit       # pip-audit dependency CVE scan
```

`make codeql` (if the CodeQL CLI is installed) and `make mutation` (slow,
on-demand) are optional locally; CI runs CodeQL on every PR.

**Definition of done:** `make check` + `make typecheck` clean, `make cov` green
(total ≥80% AND every module ≥80%), CI green. A green local suite is necessary
but not sufficient — see the ranking-quality note below.

## Conventions

- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/);
  a gitmoji prefix is optional (`✨ feat(search): …` and `feat(search): …` both
  pass). Enforced by the commitizen commit-msg hook.
- **Architecture decisions** go in `docs/adr/` — read the recent ones before a
  structural change; the boundaries (embedder / vector store / reranker / LLM)
  go through the Protocols in `backend/interfaces.py` (ADR 0021).
- **Search ranking is the real behavioral contract.** The mocked suite verifies
  plumbing, not ranking quality. If you touch ranking, run
  `scripts/eval_search.py` and compare before/after — a green suite does not
  prove nDCG held.
- **Degrade, don't crash:** LLM / config failures degrade (e.g. to BM25), never
  hard-fail search.
- **No secrets** in commits — keys live in `.env` (git-ignored); see
  `.env.example`. Security policy: [SECURITY.md](SECURITY.md).

## Reporting

- Bugs / features: open an issue.
- Security: see [SECURITY.md](SECURITY.md).
