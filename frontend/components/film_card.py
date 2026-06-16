"""Film card component — thin wrapper over media_card_frame.

Click affordance defaults to `/film/{film_id}`; pass on_click=False to
disable navigation entirely (used by edit flows where the card should
not double as a link) or pass a callable to override.
"""

from nicegui import ui

from frontend.api_client import api
from frontend.components.badges import score_badge
from frontend.components.media_card import media_card_frame
from frontend.components.theme import DIM_COLORS, score_color
from frontend.i18n import t


def film_card(film: dict, show_score: bool = False, on_click=None):
    film_id = film.get("film_id", "")
    if on_click is False:
        nav = None
    elif on_click:
        nav = on_click
    elif film_id:
        nav = lambda fid=film_id: ui.navigate.to(f"/film/{fid}")  # noqa: E731
    else:
        nav = None

    with media_card_frame(
        poster_chain=(film.get("poster_url"),),
        title_zh=film.get("title_zh", ""),
        title_en=film.get("title_en") or None,
        on_click=nav,
    ):
        if show_score and "score" in film:
            score_badge(film["score"])

        # Backend orders tags by confidence DESC; slice 5 picks the strongest
        # signals first (the "為什麼是這 5 個" fix). See db.get_film_tags.
        tags = film.get("tags", film.get("matched_tags", []))
        if tags:
            with ui.row().classes("gap-1 flex-wrap"):
                for tag_id in tags[:5]:
                    ui.badge(api.tag_label(tag_id), color="primary").props("outline").classes(
                        "text-xs"
                    )

        # Explainability — ONE template for every card: 「符合 [tag chips]」,
        # with the recall path (語意/推想/字面) demoted to a grey trailer.
        # Injected hits ("inject" source: carried a strong requested tag but
        # weren't recalled) simply have no trailer. Similar-films context
        # (no sources at all) keeps its own 共同 prefix — shared traits, not
        # query conditions.
        explain = film.get("explain")
        if explain:
            _src = {
                "vector": t("card.src_vector"),
                "hyde": t("card.src_hyde"),
                "bm25": t("card.src_bm25"),
            }
            sources = explain.get("sources", [])
            srcs = [_src[s] for s in sources if s in _src]
            prefs = explain.get("matched_prefs", [])
            if srcs or prefs:
                with ui.row().classes("gap-1 flex-wrap items-center q-mt-xs"):
                    if prefs:
                        prefix = t("card.shared") if not sources else t("card.match")
                        ui.label(prefix).classes("text-xs text-grey")
                        for p in prefs[:4]:
                            ui.badge(p, color="teal").props("outline").classes("text-xs")
                        if srcs:
                            ui.label("· " + "+".join(srcs)).classes("text-xs text-grey-7")
                    else:
                        # Recalled but matched no requested tag — show the path.
                        ui.label(t("card.hit", srcs="+".join(srcs))).classes("text-xs text-grey")


def tag_chip(tag: dict, clickable: bool = False, on_click=None):
    """Single tag chip with dimension coloring — used by the tag browser."""
    dim = tag.get("dimension", "")
    color = DIM_COLORS.get(dim, "grey")
    label = tag.get("label_zh_tw", tag.get("tag_id", ""))
    badge = ui.badge(label, color=color)
    if clickable and on_click:
        badge.on("click", on_click)
    return badge


def confidence_bar(confidence: float, label: str = ""):
    """Confidence progress bar — shared theme.score_color thresholds."""
    with ui.row().classes("items-center gap-2 w-full"):
        if label:
            ui.label(label).classes("text-xs w-20")
        ui.linear_progress(value=confidence, color=score_color(confidence)).classes("flex-grow")
        ui.label(f"{confidence:.0%}").classes("text-xs w-10 text-right")
