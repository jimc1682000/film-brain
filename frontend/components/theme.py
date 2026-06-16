"""Shared visual constants. Single source of truth for dimension colors /
labels / order, score-color thresholds, and CATCHPLAY+ palette tokens used
across components and pages.

The actual NiceGUI palette is set once in `frontend/app.py` via `ui.colors()`
+ `ui.add_head_html()`. This module exposes the underlying constants so
helpers can also reach for the same hex values when raw styling is needed
(e.g. award orange highlights inside `ui.html`).
"""

# CATCHPLAY+ brand palette extracted from catchplay.com/tw on 2026-05-26.
PALETTE = {
    "primary": "#f26f21",  # brand orange — CTAs / active state
    "primary_hover": "#ff944c",
    "primary_tap": "#d4570c",
    "bg": "#000000",  # page background
    "surface": "#1f1f1f",  # card background
    "surface_alt": "#121212",  # nested panel background
    "text": "#efefef",
    "text_muted": "#999999",
    "border": "#525252",
    "positive": "#1ac130",
    "warning": "#f2a93b",
    "negative": "#d0021b",
    "info": "#00a3d9",
}

# Dimension → Quasar color name (badges, accents). Keep aligned with the
# NiceGUI Quasar palette; semantic-feeling names so the design intent is
# obvious without chasing hex codes.
DIM_COLORS: dict[str, str] = {
    "genre": "blue",
    "theme": "purple",
    "emotion": "pink",
    "narrative": "orange",
    "setting": "green",
    "era": "brown",
    "region": "teal",
    "audience": "cyan",
    "content-type": "indigo",
    "source": "amber",
    "ip": "deep-purple",
    "occasion": "red",
    "curation": "blue-grey",
    "curation-award": "amber",
    "award": "yellow",
}


# Localised dimension labels for headings + group titles — copy lives in the
# locale table (`dim.<dimension>` keys), not here.
def dim_label(dimension: str) -> str:
    from frontend.i18n import t

    label = t(f"dim.{dimension}")
    # t() falls back to the key itself for unknown dims — show the raw id.
    return dimension if label == f"dim.{dimension}" else label


# Display order for dimension-grouped tag grids. Editor-priority order:
# the "策展切角" dimensions first, factual dimensions next, taxonomy
# bookkeeping last.
DIM_ORDER: list[str] = [
    "genre",
    "theme",
    "emotion",
    "narrative",
    "setting",
    "era",
    "region",
    "audience",
    "content-type",
    "source",
    "ip",
    "occasion",
    "curation",
    "curation-award",
    "award",
]


def score_color(value: float) -> str:
    """Quasar color name for a 0-1 score / confidence value.

    Single threshold set used by every place that paints a numeric score
    badge or progress bar — film_card, tag_list, detail page.
    """
    if value >= 0.7:
        return "positive"
    if value >= 0.4:
        return "warning"
    return "negative"
