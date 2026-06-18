"""Shared navigation + screenshot helpers for the e2e-ui tests."""

from __future__ import annotations

from pathlib import Path


def open_page(page, base_url: str, path: str) -> None:
    """Navigate + wait for NiceGUI to finish its websocket-driven render."""
    page.goto(f"{base_url}{path}", wait_until="networkidle")
    page.wait_for_timeout(2500)


def snap(page, artifacts: Path, name: str) -> None:
    """Save a full-page screenshot artifact — for human eyeballing on failure,
    NOT a pixel-diff baseline."""
    page.screenshot(path=str(artifacts / f"{name}.png"), full_page=True)
