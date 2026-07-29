# Contributing

Thanks for your interest. This is a prototype, but it ships with real quality
gates — please keep them green.

## Dev setup

The simplest path is Docker — it brings up the backing services and seeds in
one place:

```bash
make up      # backend + frontend + Qdrant + Ollama
make seed    # seed the bundled mock dataset (no API key needed)
```

Host-Python path (more setup). The seed and the backend need Qdrant (`:6333`)
and Ollama (`:11434`, with the `bge-m3` model pulled) reachable first —
otherwise seeding fails connecting to them:

```bash
# Python 3.11+. Install dev deps + the hook runner (pre-commit is not in .[dev]):
pip install -e ".[dev]"
pip install pre-commit

# Wire the commit-msg + pre-commit hooks (once per clone):
pre-commit install

# Start Qdrant + Ollama first (e.g. `docker compose up -d qdrant ollama` and
# `ollama pull bge-m3`), THEN seed the bundled mock dataset:
python -m scripts.seed_from_file data/films.seed.json

# Run the backend / frontend (separate terminals):
uvicorn backend.main:app --reload --port 8000   # http://localhost:8000/api/docs
python -m frontend.app
```

> One pre-commit hook (`betterleaks`, the staged secret scan) shells out to a
> `betterleaks` binary that isn't a Python package — install it separately. You
> can skip it locally with `SKIP=betterleaks,betterleaks-history git commit …`,
> but then scan for secrets yourself before pushing: there is no blocking
> server-side gate (the GitGuardian App flags PRs but is advisory, and `master`
> is not branch-protected). Keeping the local hook on is the real safety net.

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

## PR merge automation

`.github/workflows/pr-merge-automation.yml` applies this narrow policy:

- Every PR is classified with one risk label: `risk:low`, `risk:medium`,
  `risk:high`, or `risk:manual-only`.
- Only `risk:low` PRs merge automatically after all checks are green and there
  are no unresolved review threads. The workflow comments `Looks Good` before
  squash-merging with the current head SHA.
- All other risks request `@codex review` for the current head and add
  `needs:codex-review`, but never merge automatically. Follow the dotfiles
  CONTRIBUTING rule manually: if a comment appears, either fix it or reply with
  the reason it should not be adopted before merging.
