"""Award nominee card — same outer frame as film_card, award-specific body."""

from nicegui import ui

from frontend.components.badges import rating_badge
from frontend.components.media_card import media_card_frame
from frontend.components.poster import award_poster_chain
from frontend.i18n import t


def _display_title(nom: dict) -> str:
    """When matched to a CATCHPLAY+ film, prefer our own title so the awards
    card visually agrees with the film detail page (poster ordering already
    handled in award_poster_chain)."""
    if nom.get("matched_film_id"):
        return (
            nom.get("matched_title_zh")
            or nom.get("tmdb_title")
            or nom.get("film_title_primary")
            or ""
        )
    return (
        nom.get("tmdb_title") or nom.get("film_title_primary") or nom.get("matched_title_zh") or ""
    )


def _on_click(nom: dict):
    """Match → film detail page; else → external TMDB page in a new tab."""
    matched_fid = nom.get("matched_film_id")
    if matched_fid:
        return lambda fid=matched_fid: ui.navigate.to(f"/film/{fid}")
    tid = nom.get("tmdb_id")
    if tid:
        mt = nom.get("tmdb_media_type") or "movie"
        return lambda tid=tid, mt=mt: ui.navigate.to(
            f"https://www.themoviedb.org/{mt}/{tid}", new_tab=True
        )
    return None


def award_card(nom: dict):
    """Render one award nominee — see /api/awards/nominees for data shape."""
    display_en = (
        nom.get("film_title_primary")
        if nom.get("tmdb_title") != nom.get("film_title_primary")
        else None
    )
    matched_fid = nom.get("matched_film_id")
    result = (nom.get("result") or "").lower()

    with media_card_frame(
        poster_chain=award_poster_chain(nom),
        title_zh=_display_title(nom),
        title_en=display_en,
        on_click=_on_click(nom),
    ):
        with ui.row().classes("items-center gap-1 flex-wrap"):
            if nom.get("tmdb_year"):
                ui.label(str(nom["tmdb_year"])).classes("text-caption text-grey")
            if nom.get("tmdb_vote_avg"):
                rating_badge(nom["tmdb_vote_avg"], prefix="TMDb ★")

        ui.label(nom.get("category", "")).classes("text-caption text-primary")
        if nom.get("person"):
            ui.label(nom["person"]).classes("text-caption text-grey")

        with ui.row().classes("items-center gap-1 q-mt-xs"):
            if result == "won":
                ui.badge(t("card.award_won"), color="positive").props("outline").classes("text-xs")
            else:
                ui.badge(t("card.award_nominee"), color="primary").props("outline").classes(
                    "text-xs"
                )

            if matched_fid:
                ui.badge(t("card.in_library"), color="green").classes("text-xs")
            else:
                ui.badge(t("card.not_in_library"), color="grey").props("outline").classes("text-xs")

        overview = nom.get("tmdb_overview") or ""
        if overview:
            ui.label(overview[:80] + ("…" if len(overview) > 80 else "")).classes(
                "text-caption text-grey line-clamp-2"
            )
