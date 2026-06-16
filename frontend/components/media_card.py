"""Shared poster-card frame for film and award nominee cards.

Both components show a clickable card with the same outer shape: poster
on top, a metadata column below (title, optional english title, badges,
free-form body). The frame is identical; only the body content differs.

Usage:

    with media_card_frame(
        poster_chain=(film["poster_url"],),
        title_zh=film["title_zh"],
        title_en=film.get("title_en"),
        on_click=lambda: ui.navigate.to(f"/film/{film_id}"),
    ):
        # whatever extra rows the caller wants
        ui.label("...")
"""

from collections.abc import Callable, Iterable
from contextlib import contextmanager

from nicegui import ui

from frontend.components.poster import render_poster

CARD_CLASSES = "w-full min-w-0 cursor-pointer hover:shadow-lg transition-shadow"
CARD_CLASSES_STATIC = "w-full min-w-0"


@contextmanager
def media_card_frame(
    *,
    poster_chain: Iterable[str | None],
    title_zh: str,
    title_en: str | None = None,
    on_click: Callable[[], None] | None = None,
):
    """Yields the column under the title so the caller can append its rows.

    Pass on_click=None to skip the clickable affordance (used by detail-page
    related-films lists that already navigate via film_card defaults).
    """
    card = ui.card().classes(CARD_CLASSES if on_click else CARD_CLASSES_STATIC)
    if on_click:
        card.on("click", on_click)
    with card:
        render_poster(*poster_chain)
        with ui.column().classes("p-3 gap-1") as body:
            ui.label(title_zh).classes("text-subtitle1 font-bold line-clamp-1")
            if title_en:
                ui.label(title_en).classes("text-caption text-grey line-clamp-1")
            yield body
