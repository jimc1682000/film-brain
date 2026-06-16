"""Unified dimension-grouped tag grid — shared by detail page and auto-tag results.

Semantics: every tag has a `status` of `saved` (already in DB) or `suggested`
(fresh from AI / preview). A single checkbox drives user intent — ☑ = keep/add,
☐ = drop/skip. The differing API routes at save time derive from `status`, not
from a separate source/target split.
"""

from collections.abc import Callable

from nicegui import app, ui

from frontend.components.layout import TAG_GRID
from frontend.components.theme import DIM_COLORS, DIM_ORDER, dim_label, score_color
from frontend.i18n import t

# Confidence ring (non-default style): conic-gradient donut + % in the centre,
# 3-colour by tier — replaces the long progress bar with a compact gauge.
_SC_HEX = {"positive": "#6ed496", "warning": "#e8c45e", "negative": "#e0655c"}

# Compact (ring) view → 4 explicit flex columns. Cards are distributed greedily
# in Python (shortest column first) so all 4 columns fill and stay roughly
# level — true masonry, unlike CSS column-count which collapses to fewer
# columns around a tall card.
_COLS_CSS = """
<style>
  .tg-cols { display:flex; gap:14px; align-items:flex-start; width:100%; }
  .tg-col { flex:1 1 0; min-width:0; display:flex; flex-direction:column; gap:14px; }
  @media (max-width:760px) { .tg-cols { flex-wrap:wrap; } .tg-col { flex-basis:calc(50% - 7px); } }
</style>
"""


def _conf_ring(conf: float) -> None:
    # SVG donut (stroke-dasharray on a circumference-100 circle) — bulletproof
    # vs conic-gradient quirks; always a true circle, colour by tier.
    hexc = _SC_HEX.get(score_color(conf), "#888888")
    pct = max(0, min(100, round(conf * 100)))
    ui.html(
        f'<svg width="40" height="40" viewBox="0 0 36 36" style="flex:0 0 auto">'
        f'<circle cx="18" cy="18" r="15.9155" fill="none" stroke="#2c2c2c" stroke-width="3"/>'
        f'<circle cx="18" cy="18" r="15.9155" fill="none" stroke="{hexc}" stroke-width="3"'
        f' stroke-dasharray="{pct} 100" transform="rotate(-90 18 18)" stroke-linecap="round"/>'
        f'<text x="18" y="18" text-anchor="middle" dominant-baseline="central"'
        f' font-size="8.5" font-weight="800" fill="#efefef">{pct}%</text>'
        f"</svg>"
    )


def default_checked(tags: list[dict]) -> set[str]:
    """Uniform preset: confidence ≥ 0.6 → checked. Applied to both saved + suggested."""
    return {tag["tag_id"] for tag in tags if tag.get("confidence", 0) >= 0.6 and tag.get("tag_id")}


def tag_grid(
    tags: list[dict],
    *,
    editable: bool = False,
    checked: set[str] | None = None,
    initial: set[str] | None = None,
    on_toggle: Callable[[str, bool], None] | None = None,
    on_toggle_many: Callable[[list[str], bool], None] | None = None,
    columns: int = 2,
) -> None:
    """Render dimension-grouped tag cards.

    Each tag dict: {tag_id, dimension, label_zh_tw, confidence, status, reasoning?}

    - editable=False: read-only (no checkboxes)
    - editable=True: checkbox per row; visual cues driven by (initial, checked)
      - was in initial but now unchecked → opacity + strikethrough (removal preview)
      - not in initial but now checked → green dot (addition preview)
    """
    checked = checked if checked is not None else set()
    initial = initial if initial is not None else set(checked)

    by_dim: dict[str, list[dict]] = {}
    for tag in tags:
        by_dim.setdefault(tag.get("dimension") or "unknown", []).append(tag)

    sorted_dims = [d for d in DIM_ORDER if d in by_dim] + [d for d in by_dim if d not in DIM_ORDER]
    _ = columns  # kept for signature compatibility; layout is responsive
    compact = app.storage.user.get("style", "default") != "default"

    def _dim_card(dim: str) -> None:
        dim_tags = sorted(by_dim[dim], key=lambda x: -x.get("confidence", 0))
        color = DIM_COLORS.get(dim, "grey")
        with ui.card().classes("q-pa-sm w-full" if compact else "q-pa-md"):
            with ui.row().classes("items-center gap-2"):
                if editable and (on_toggle or on_toggle_many):
                    dim_ids = [tag["tag_id"] for tag in dim_tags if tag.get("tag_id")]
                    all_on = bool(dim_ids) and all(tid in checked for tid in dim_ids)
                    dim_cb = (
                        ui.checkbox(value=all_on).props("dense").tooltip(t("taglist.toggle_all"))
                    )
                    dim_cb.on_value_change(_make_dim_toggle(dim_ids, on_toggle_many, on_toggle))
                ui.badge(dim_label(dim), color=color).classes("text-sm")
                ui.label(t("taglist.count", n=len(dim_tags))).classes("text-caption text-grey")
            ui.separator().classes("q-my-xs")
            for tag in dim_tags:
                _render_row(tag, editable, checked, initial, on_toggle)

    if compact:
        # Greedy 4-column masonry: drop each card into the currently shortest
        # column so all four columns fill and stay roughly level.
        ui.add_head_html(_COLS_CSS)
        ncols = 4
        cols: list[list[str]] = [[] for _ in range(ncols)]
        heights = [0.0] * ncols
        for dim in sorted(sorted_dims, key=lambda d: -len(by_dim[d])):
            i = heights.index(min(heights))
            cols[i].append(dim)
            heights[i] += 1.6 + len(by_dim[dim])  # header + one row per tag
        with ui.element("div").classes("tg-cols"):
            for col in cols:
                with ui.element("div").classes("tg-col"):
                    for dim in col:
                        _dim_card(dim)
    else:
        with ui.grid().classes(f"{TAG_GRID} gap-4 w-full"):
            for dim in sorted_dims:
                _dim_card(dim)


def _render_row(
    tag: dict,
    editable: bool,
    checked: set[str],
    initial: set[str],
    on_toggle: Callable[[str, bool], None] | None,
) -> None:
    tag_id = tag.get("tag_id", "")
    label = tag.get("label_zh_tw") or tag.get("label_en") or tag_id
    conf = float(tag.get("confidence", 0) or 0)
    status = tag.get("status", "saved")
    reasoning = tag.get("reasoning") or ""

    is_checked = tag_id in checked
    was_initial = tag_id in initial
    removed = editable and was_initial and not is_checked
    added = editable and (not was_initial) and is_checked

    bar_color = score_color(conf)

    with ui.column().classes("w-full gap-0 q-my-xs"):
        row_cls = "items-center gap-2 w-full"
        if removed:
            row_cls += " opacity-40"
        with ui.row().classes(row_cls):
            if editable and on_toggle is not None:
                cb = ui.checkbox(value=is_checked)
                cb.on_value_change(_make_toggle(tag_id, on_toggle))
            label_cls = "text-sm"
            if removed:
                label_cls += " line-through"
            ui.label(str(label)).classes(label_cls)
            if status == "suggested" and editable:
                ui.badge(t("taglist.ai"), color="blue-grey").props("outline").classes("text-xs")
            if added:
                ui.badge(t("taglist.added"), color="positive").classes("text-xs")
            ui.space()
            # Non-default style → compact confidence ring; default → long bar.
            if app.storage.user.get("style", "default") != "default":
                _conf_ring(conf)
            else:
                ui.linear_progress(value=conf, color=bar_color, show_value=False).classes("w-32")
                ui.label(f"{conf:.0%}").classes("text-xs w-10 text-right text-grey")
        if reasoning:
            reasoning_cls = "text-xs text-grey q-ml-md"
            if removed:
                reasoning_cls += " opacity-40"
            ui.label(reasoning).classes(reasoning_cls)


def _make_toggle(tag_id: str, on_toggle: Callable[[str, bool], None]):
    def handler(e):
        on_toggle(tag_id, bool(e.value))

    return handler


def _make_dim_toggle(
    dim_ids: list[str],
    on_toggle_many: Callable[[list[str], bool], None] | None,
    on_toggle: Callable[[str, bool], None] | None,
):
    def handler(e):
        val = bool(e.value)
        if on_toggle_many is not None:
            on_toggle_many(dim_ids, val)
        elif on_toggle is not None:
            for tid in dim_ids:
                on_toggle(tid, val)

    return handler


def merge_saved_and_suggestions(saved: list[dict], suggestions: list[dict]) -> list[dict]:
    """Merge into unified list; dedupe by tag_id (saved wins, suggestion reasoning kept)."""
    by_id: dict[str, dict] = {}
    for tag in saved:
        tid = tag.get("tag_id")
        if not tid:
            continue
        by_id[tid] = {**tag, "status": "saved"}
    for s in suggestions:
        tid = s.get("tag_id")
        if not tid:
            continue
        if tid in by_id:
            if s.get("reasoning") and not by_id[tid].get("reasoning"):
                by_id[tid]["reasoning"] = s["reasoning"]
            continue
        by_id[tid] = {**s, "status": "suggested"}
    return list(by_id.values())
