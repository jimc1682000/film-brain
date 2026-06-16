"""Tech-brief viewer — renders docs/judge-brief.md (markdown + mermaid) on-site.

The repo doc is the single source of truth; this page just renders it.
Layout: sticky section TOC on the left (hidden on mobile), content on the
right. Mermaid fences become ui.mermaid elements (dark theme, enlarged
font); fenced text blocks get a brand-colored callout style.
"""

from __future__ import annotations

import re
from pathlib import Path

from nicegui import ui

from frontend.i18n import t

# Resolve relative to the repo / container root (cwd is /app in Docker,
# repo root in local dev — both have docs/ as a sibling of frontend/).
_DOC_PATH = Path("docs/judge-brief.md")

_MERMAID_FENCE = re.compile(r"```mermaid\s*\n(.*?)```", re.S)
_H2 = re.compile(r"^## (.+)$", re.M)

# Brand callout for fenced text blocks + readable mermaid sizing.
_CSS = """
.brief-doc h1 { font-size: 2.6rem; }
/* Scope the typography bump to rendered markdown only — a bare
   `.brief-doc p` selector also hits the <p> inside mermaid htmlLabels,
   which mermaid measured at the default size → overflowing, clipped
   node boxes. */
.brief-doc .nicegui-markdown p, .brief-doc .nicegui-markdown li {
  font-size: 1.2rem;
  line-height: 1.8;
}
.brief-doc .mermaid p {
  font-size: inherit;
  line-height: inherit;
  margin: 0;
}
/* Tables keep a moderate size and scroll horizontally instead of
   blowing up the layout when the body copy is enlarged. */
.brief-doc table { display: block; overflow-x: auto; max-width: 100%; }
.brief-doc td, .brief-doc th {
  font-size: 1rem;
  line-height: 1.6;
  padding: 6px 12px;
  white-space: normal;
}
.brief-doc pre {
  background: rgba(242, 111, 33, 0.10) !important;
  border: 1px solid #f26f21;
  border-radius: 8px;
  padding: 14px 18px;
}
.brief-doc pre code {
  color: #ffb380;
  font-size: 1.05rem;
  line-height: 1.7;
  white-space: pre-wrap;
}
/* Diagrams render at natural size (vertical TB layouts are narrow and
   readable as-is) and never overflow the column. Full-width stretching
   blew the tall charts up after the typography bump. */
.brief-doc .mermaid { display: flex; justify-content: center; }
.brief-doc .mermaid svg {
  max-width: 100% !important;
  height: auto;
}
"""

# htmlLabels (needs securityLevel loose) lets the browser lay out CJK + emoji
# labels; SVG-text mode measures them too narrow and clips at the box edge.
# Passed via mermaid.initialize (the per-diagram %%init%% directive can't
# relax securityLevel).
_MERMAID_CONFIG = {
    "theme": "dark",
    "securityLevel": "loose",
    "flowchart": {"htmlLabels": True, "padding": 12},
    "themeVariables": {"fontFamily": "sans-serif"},
}


def _sections(text: str) -> list[tuple[str, str]]:
    """Split the doc on `## ` headings → [(title, body)]; first title is ''."""
    parts = _H2.split(text)
    out = [("", parts[0])]
    for i in range(1, len(parts), 2):
        out.append((parts[i], parts[i + 1]))
    return out


def _render_blocks(md: str) -> None:
    """Markdown chunks via ui.markdown, mermaid fences via ui.mermaid."""
    for i, part in enumerate(_MERMAID_FENCE.split(md)):
        if not part.strip():
            continue
        if i % 2:
            ui.mermaid(part, config=_MERMAID_CONFIG).classes("w-full")
        else:
            ui.markdown(part, extras=["tables", "fenced-code-blocks"]).classes("w-full")


def brief_page() -> None:
    if not _DOC_PATH.exists():
        ui.label(t("brief.missing")).classes("text-grey q-mt-lg")
        return

    ui.add_css(_CSS)
    sections = _sections(_DOC_PATH.read_text(encoding="utf-8"))

    # Floating hamburger TOC — fixed position, opens a menu of section
    # links, so the content column never shifts.
    with (
        ui.button(icon="menu")
        .props("fab-mini color=primary")
        .style("position: fixed; left: 18px; top: 80px; z-index: 1000;")
        .tooltip(t("brief.toc"))
    ):
        with ui.menu():
            for idx, (title, _) in enumerate(sections):
                if title:
                    ui.menu_item(
                        title,
                        on_click=lambda i=idx: ui.run_javascript(
                            f"document.getElementById('sec{i}')"
                            ".scrollIntoView({behavior: 'smooth'})"
                        ),
                    )

    with ui.column().classes("brief-doc w-full q-mx-auto").style("max-width: 900px;"):
        for idx, (title, body) in enumerate(sections):
            with ui.element("div").props(f'id="sec{idx}"').classes("w-full"):
                if title:
                    ui.markdown(f"## {title}")
                _render_blocks(body)
