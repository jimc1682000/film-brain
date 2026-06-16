"""Feedback wiki page storage — frontmatter + markdown on filesystem.

Pages live at `{settings.feedback_dir}/{page_id}.md`. Reserved files
(`SCHEMA.md`, `index.md`, `log.md`) are excluded from page listings.
"""

import contextlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

from backend.config import settings
from backend.models import FeedbackPage

_RESERVED = {"SCHEMA.md", "index.md", "log.md"}


def _root() -> Path:
    return settings.feedback_dir


def _page_path(page_id: str) -> Path:
    if page_id.endswith(".md"):
        page_id = page_id[:-3]
    if ".." in page_id.split("/"):
        raise ValueError(f"invalid page_id: {page_id}")
    return _root() / f"{page_id}.md"


def _parse(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return {}, raw
    fm_text = raw[4:end]
    body = raw[end + 5 :]
    fm = yaml.safe_load(fm_text) or {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, body.lstrip("\n")


def _serialize(fm: dict, body: str) -> str:
    fm_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).rstrip()
    return f"---\n{fm_text}\n---\n\n{body.rstrip()}\n"


def _relative_page_id(path: Path) -> str:
    rel = path.relative_to(_root()).as_posix()
    return rel[:-3] if rel.endswith(".md") else rel


def list_pages() -> list[FeedbackPage]:
    root = _root()
    if not root.exists():
        return []
    pages: list[FeedbackPage] = []
    for md in sorted(root.rglob("*.md")):
        if md.name in _RESERVED and md.parent == root:
            continue
        page_id = _relative_page_id(md)
        page = _load_from_path(md, page_id, include_body=False)
        if page:
            pages.append(page)
    return pages


def get_page(page_id: str) -> FeedbackPage | None:
    path = _page_path(page_id)
    if not path.exists():
        return None
    return _load_from_path(path, page_id, include_body=True)


def _load_from_path(path: Path, page_id: str, *, include_body: bool) -> FeedbackPage | None:
    raw = path.read_text(encoding="utf-8")
    fm, body = _parse(raw)
    try:
        return FeedbackPage(
            page_id=page_id,
            kind=fm.get("kind", "tags"),
            title=fm.get("title", page_id),
            status=fm.get("status", "open"),
            merged_into=fm.get("merged_into"),
            resolved_at=_coerce_dt(fm.get("resolved_at")),
            resolution_note=fm.get("resolution_note"),
            updated_at=_coerce_dt(fm.get("updated_at")),
            model_used=fm.get("model_used"),
            consultant_validated=bool(fm.get("consultant_validated", False)),
            confidence=fm.get("confidence"),
            sources=list(fm.get("sources") or []),
            body=body if include_body else "",
        )
    except Exception:
        return None


def _coerce_dt(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def apply_reanalyze(
    page_id: str,
    frontmatter_updates: dict,
    body_section_title: str,
    body_section_md: str,
    model_used: str,
) -> FeedbackPage:
    """Merge frontmatter updates, append new body section, atomic write."""
    path = _page_path(page_id)
    if not path.exists():
        raise FileNotFoundError(f"feedback page not found: {page_id}")

    raw = path.read_text(encoding="utf-8")
    fm, body = _parse(raw)

    now_iso = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    safe_updates = {k: v for k, v in (frontmatter_updates or {}).items() if k != "kind"}
    fm.update(safe_updates)
    fm["updated_at"] = now_iso
    fm["model_used"] = model_used
    fm["consultant_validated"] = True

    new_status = fm.get("status", "open")
    if new_status != "open" and not fm.get("resolved_at"):
        fm["resolved_at"] = now_iso

    if body_section_title and body_section_md:
        header = body_section_title.strip()
        if not header.startswith("#"):
            header = f"## {header}"
        appended = f"\n\n{header}\n\n{body_section_md.strip()}\n"
        body = body.rstrip() + appended

    _atomic_write(path, _serialize(fm, body))
    page = get_page(page_id)
    if page is None:
        raise RuntimeError(f"page vanished after write: {page_id}")
    return page


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp).replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
