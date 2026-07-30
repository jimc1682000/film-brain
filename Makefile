.PHONY: up down seed test logs clean fmt lint check recompute-similar cov e2e e2e-ui typecheck audit mutation codeql

# Python used by the local (non-docker) e2e harnesses. Prefer a repo-local
# .venv, else fall back to python3 (mirrors scripts/e2e_*.sh) so `make e2e-ui`
# works in host workflows without a .venv. Override: `make e2e-ui PYTHON=…`.
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

seed:
	docker compose exec backend python -m scripts.seed_from_file data/films.seed.json

# Canonical entry point for recomputing similar-films after the library
# changes (new imports). Slow on CPU (~30-40s/film). seed_all + the
# library-doctor skill both ultimately run this same script.
recompute-similar:
	docker compose exec backend python -m scripts.05_compute_similar

# Runs on the HOST (needs the dev extras: pip install -e .[dev]). The runtime
# image deliberately ships no test deps, so `docker compose exec backend pytest`
# is no longer a thing.
test:
	$(PYTHON) -m pytest backend/tests/ -v

logs:
	docker compose logs -f

logs-backend:
	docker compose logs -f backend

clean:
	docker compose down -v
	rm -f data/film_library.db
	rm -rf data/tmdb_enriched/*

fmt:
	ruff format backend frontend scripts

# Local test coverage (per-module >=80% gate is enforced in CI)
cov:
	$(PYTHON) -m pytest backend/tests -q --cov=backend --cov-report=term-missing --cov-fail-under=80

# Full-stack e2e smoke: real Qdrant + real embedder + live server (local only)
e2e:
	bash scripts/e2e_smoke.sh

# Browser e2e-ui: real Chromium vs live frontend+backend on mock data (local
# only, opt-in). Installs the browser harness deps, then boots + drives the UI.
# Catches render/CSS/SVG regressions the in-process suite can't see.
e2e-ui:
	$(PYTHON) -m pip install -q -r requirements-e2e.txt
	$(PYTHON) -m playwright install chromium
	PYTHON=$(PYTHON) bash scripts/e2e_ui.sh

# Static type check (backend source; config in pyproject [tool.pyright])
typecheck:
	bash scripts/typecheck.sh

# Dependency CVE scan (same ignore as the pre-push hook)
audit:
	pip-audit -r requirements.txt --ignore-vuln CVE-2025-3000

# Mutation testing — SLOW (one suite run per mutant). On-demand only, never a
# per-commit gate. `mutmut results` to inspect survivors after a run.
mutation:
	mutmut run

lint:
	ruff check backend frontend scripts --fix

check:
	ruff format --check backend frontend scripts
	ruff check backend frontend scripts

# Full local CodeQL scan — parity with the CI codeql.yml (same security-and-
# quality suite). SLOW: builds a DB (~minutes), on-demand only, NOT a gate; the
# fast local SAST is ruff S + semgrep. Needs the CodeQL CLI in PATH:
#   gh extension install github/gh-codeql   (then run `CODEQL='gh codeql' make codeql`)
#   or download: https://github.com/github/codeql-cli-binaries/releases
CODEQL ?= codeql
codeql:
	@command -v $(firstword $(CODEQL)) >/dev/null 2>&1 || \
	  { echo "CodeQL CLI not found — install: gh extension install github/gh-codeql"; exit 1; }
	$(CODEQL) database create .codeql-db --language=python --overwrite --source-root=.
	$(CODEQL) database analyze .codeql-db --download \
	  codeql/python-queries:codeql-suites/python-security-and-quality.qls \
	  --format=sarif-latest --output=codeql-results.sarif
	@echo "Wrote codeql-results.sarif — open in the VS Code SARIF Viewer."
