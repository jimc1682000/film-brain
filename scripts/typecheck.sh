#!/usr/bin/env bash
# Type-check backend with pyright (config in pyproject [tool.pyright]).
# --pythonpath resolves the CURRENT env's interpreter so installed deps
# (fastapi, pydantic…) are seen — pyright's node wrapper does NOT auto-detect
# the calling venv. Portable across pre-commit (python3-boto3), CI (runner
# python), and local (.venv): each passes its own interpreter.
set -euo pipefail
exec python -m pyright --pythonpath "$(python -c 'import sys; print(sys.executable)')"
