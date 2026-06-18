"""Fixtures for the browser e2e-ui harness.

The stack (backend + frontend) is booted by `scripts/e2e_ui.sh`, not here — a
shell `trap` tears two processes down more reliably than a pytest fixture if one
hangs. These fixtures only give the tests a base URL, a screenshot sink, and a
console-error collector.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

BASE_URL = os.environ.get("E2E_UI_BASE", "http://localhost:8087")
ARTIFACTS = Path(os.environ.get("E2E_UI_ARTIFACTS", "/tmp/e2e_ui_artifacts"))


@pytest.fixture(scope="session")
def artifacts() -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS


@pytest.fixture
def ui_base() -> str:
    # NB: not named `base_url` — pytest-base-url (pulled in by pytest-playwright)
    # reserves that as a session-scoped fixture and clashes (ScopeMismatch).
    return BASE_URL


@pytest.fixture
def errors(page) -> list[str]:
    """Browser-side errors seen during the test. We assert on `error`-level
    console messages + uncaught page errors only — Quasar/NiceGUI emit benign
    warnings that would flake a warning-level assertion."""
    found: list[str] = []
    page.on("console", lambda m: found.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: found.append(str(e)))
    return found
