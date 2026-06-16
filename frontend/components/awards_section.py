"""Per-film awards section — extracted from frontend/pages/detail.py.

Lists every award_nominees row linked to one film, grouped by ceremony
(org_id + year), with a small header summarising won vs nominated counts
and a link out to IMDb / TMDb for fuller official records.
"""

from nicegui import ui

from frontend.api_client import api
from frontend.components.messages import hint_label
from frontend.i18n import t


def render_awards_section(film_id: str, film: dict) -> None:
    """Show awards (nominations / wins) linked to this film."""
    try:
        noms = api.list_award_nominations(film_id=film_id, limit=100)
    except Exception as e:
        ui.label(t("awards.load_film_failed", e=e)).classes("text-caption text-negative")
        return

    imdb_id = film.get("imdb_id") or film.get("tmdb_imdb_id")
    tmdb_id = film.get("tmdb_id")
    wins = sum(1 for n in noms if (n.get("result") or "").lower() == "won")
    noms_cnt = len(noms) - wins

    with ui.row().classes("items-center gap-2"):
        ui.icon("emoji_events").classes("text-amber-8 text-h5")
        ui.label(t("awards.film_section_title")).classes("text-h5 font-bold")
        if noms:
            ui.badge(t("awards.wins", n=wins), color="positive").classes("text-sm")
            ui.badge(t("awards.nominations", n=noms_cnt), color="primary").props("outline").classes(
                "text-sm"
            )
        ui.space()
        if imdb_id:
            ui.link(
                t("awards.imdb_link"),
                f"https://www.imdb.com/title/{imdb_id}/awards/",
                new_tab=True,
            ).classes("text-caption text-grey")
        elif tmdb_id:
            ui.link(
                t("awards.tmdb_link"),
                f"https://www.themoviedb.org/movie/{tmdb_id}",
                new_tab=True,
            ).classes("text-caption text-grey")

    if not noms:
        hint_label(t("awards.none_for_film"))
        return

    by_org: dict[tuple[str, int], list[dict]] = {}
    for n in noms:
        key = (n.get("org_id") or "", n.get("year") or 0)
        by_org.setdefault(key, []).append(n)

    # Newest ceremonies first, then alphabetical by org_id within a year.
    for (org_id, year), items in sorted(by_org.items(), key=lambda kv: (-kv[0][1], kv[0][0])):
        org_label = items[0].get("tag_label_zh_tw", "") or org_id
        org_name = org_label.split("—")[0].strip() if "—" in org_label else org_id
        with ui.row().classes("items-center gap-2 q-mt-sm"):
            ui.icon("military_tech").classes("text-amber-9")
            ui.label(f"{org_name} {year}").classes("text-subtitle1 font-bold")
        with ui.column().classes("gap-1 q-ml-md"):
            for n in items:
                result = (n.get("result") or "").lower()
                icon = "🏆" if result == "won" else "▪"
                cat = n.get("category") or "—"
                person = n.get("person") or ""
                suffix = f" — {person}" if person else ""
                cls = "text-body2 text-positive font-bold" if result == "won" else "text-body2"
                ui.label(f"{icon} {cat}{suffix}").classes(cls)
