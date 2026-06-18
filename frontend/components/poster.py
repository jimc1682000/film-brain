"""Unified poster image rendering.

Both `film_card` and `award_card` reach for a poster URL with a fallback
chain and reproduce the same placeholder div when no URL is available.
The award flow has an extra constraint: when the nominee maps to a film
we own, the CATCHPLAY+ artwork must beat the TMDB still (otherwise the
awards page renders a different poster than the detail page, which an
editor caught during review). This helper bakes those rules in one place.

Usage:

    render_poster(film["poster_url"])              # film card
    render_poster(*award_poster_chain(nominee))    # award card
"""

from collections.abc import Callable, Iterable

from nicegui import ui

POSTER_CLASSES = "w-full h-40 object-cover rounded-t"
PLACEHOLDER_CLASSES = "w-full h-40 bg-grey-10 rounded-t flex items-center justify-center"
# CP+ brand logo, served from frontend/assets via app.add_static_files.
CP_PLACEHOLDER_LOGO = "/assets/cp-logo.png"


def _is_real_url(value: str | None) -> bool:
    """A usable poster URL. Accepts http(s) and generated title-card SVGs
    (`data:image/svg+xml`); rejects the 1×1 lazy-load `data:image/gif…`
    placeholder the catchplay scraper sometimes captured (treated as missing).
    """
    if not value:
        return False
    if value.startswith("data:"):
        return value.startswith("data:image/svg+xml")
    return True


def render_poster(
    *candidates: str | None,
    classes: str = POSTER_CLASSES,
    placeholder_classes: str = PLACEHOLDER_CLASSES,
    on_click: Callable[[], None] | None = None,
) -> None:
    """Render the first usable URL among `candidates`, else the CP+ logo.

    Priority is the caller's responsibility (CATCHPLAY+ before TMDB);
    this just picks the first non-placeholder URL. When none qualify it
    renders the CATCHPLAY+ logo centered on a dark tile.
    """
    url = next((c for c in candidates if _is_real_url(c)), "")
    if url:
        img = ui.image(url).classes(classes)
        if on_click:
            img.on("click", on_click)
        return
    with ui.element("div").classes(placeholder_classes) as ph:
        ui.image(CP_PLACEHOLDER_LOGO).classes("w-1/3 max-w-24 opacity-80")
    if on_click:
        ph.on("click", on_click)


def award_poster_chain(nominee: dict) -> Iterable[str | None]:
    """Return the priority-ordered URL list for an award nominee card.

    Rule: if the nominee maps to a CATCHPLAY+ film, our own artwork wins
    so the awards page and the film detail page show the same poster.
    Otherwise fall back to TMDB, which is the only image we have for
    nominees that are not in the library.
    """
    if nominee.get("matched_film_id"):
        return (nominee.get("matched_poster_url"), nominee.get("tmdb_poster_url"))
    return (nominee.get("tmdb_poster_url"), nominee.get("matched_poster_url"))
