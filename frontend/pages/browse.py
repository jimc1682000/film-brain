"""Page 3 — Tag Browse."""

from nicegui import app, ui

from frontend.api_client import api
from frontend.components import film_list
from frontend.components.messages import error_label
from frontend.components.page_header import page_header
from frontend.components.theme import DIM_COLORS
from frontend.i18n import t

PAGE_SIZE = 8
RECENT_POOL = 300


def browse_page():
    """Build the tag browsing page."""
    # films_container created inside splitter.after (see Layout section) —
    # wrap in list so nested fns close over the slot.
    films_container_ref: list[ui.column] = []
    stats_container = ui.row()

    def _make_tag_click(tag_id: str, tag_label: str):
        def handler():
            show_tag_films(tag_id, tag_label)

        return handler

    def load_dimensions():
        try:
            return api.get_dimensions()
        except Exception:
            return []

    def show_dimension_tags(dimension: str):
        try:
            data = api.list_tags(dimension=dimension)
            return data.get("tags", [])
        except Exception:
            return []

    def show_tag_films(tag_id: str, tag_label: str):
        if not films_container_ref:
            return
        container = films_container_ref[0]
        container.clear()
        try:
            data = api.get_films_by_tag(tag_id)
            films = data.get("films", [])
            with container:
                ui.label(t("browse.films_tagged", label=tag_label, n=len(films))).classes(
                    "text-h6 q-mb-md"
                )
                if not films:
                    ui.label(t("browse.no_films")).classes("text-grey")
                else:
                    film_list.render(films, app.storage.user.get("style", "default"))
        except Exception as e:
            with container:
                error_label(t("browse.error", e=e))

    # --- Layout ---
    page_header(t("browse.title"), t("browse.desc"))

    # Recently tagged films — paginated grid
    try:
        recent = api.recent_tag_activity(limit=RECENT_POOL).get("films", [])
    except Exception:
        recent = []

    if recent:
        ui.label(t("browse.recent")).classes("text-h6 q-mb-sm")
        recent_grid = ui.element("div").classes("w-full q-mb-sm")
        state = {"shown": PAGE_SIZE}
        load_more_btn = ui.button(t("browse.load_more"), icon="expand_more").props(
            "outline color=primary"
        )

        def render_recent():
            recent_grid.clear()
            with recent_grid:
                film_list.render(recent[: state["shown"]], app.storage.user.get("style", "default"))
            if state["shown"] >= len(recent):
                load_more_btn.visible = False
            else:
                load_more_btn.visible = True

        def load_more():
            state["shown"] += PAGE_SIZE
            render_recent()

        load_more_btn.on("click", load_more)
        render_recent()
        ui.separator().classes("q-my-md")

    dimensions = load_dimensions()

    # Stats dashboard
    try:
        total_films = api.list_films(limit=1).get("total", 0)
    except Exception:
        total_films = 0

    with stats_container.classes("gap-4 q-mb-lg flex-wrap"):
        used_dims = sum(1 for d in dimensions if d.get("used_tag_count", 0) > 0)
        used_tags = sum(d.get("used_tag_count", 0) for d in dimensions)
        with ui.card().classes("p-3"):
            ui.label(str(total_films)).classes("text-h4 text-primary")
            ui.label(t("browse.stat_films")).classes("text-caption")
        with ui.card().classes("p-3"):
            ui.label(str(used_dims)).classes("text-h4 text-primary")
            ui.label(t("browse.stat_dims")).classes("text-caption")
        with ui.card().classes("p-3"):
            ui.label(str(used_tags)).classes("text-h4 text-primary")
            ui.label(t("browse.stat_tags")).classes("text-caption")

    # Main content: left accordion + right film grid
    with ui.splitter(value=35).classes("w-full") as splitter:
        with splitter.before, ui.column().classes("w-full"):
            for dim in dimensions:
                dim_name = dim["dimension"]
                count = dim["tag_count"]
                color = DIM_COLORS.get(dim_name, "grey")

                with ui.expansion(
                    f"{dim_name} ({count})",
                    icon="folder",
                ).classes("w-full"):
                    tags = show_dimension_tags(dim_name)
                    with ui.column().classes("gap-1"):
                        for tag in tags:
                            tag_label = tag.get("label_zh_tw", tag["tag_id"])
                            ui.button(
                                f"{tag_label} ({tag.get('label_en', '')})",
                                on_click=_make_tag_click(tag["tag_id"], tag_label),
                            ).props(f"flat dense color={color}").classes("text-left justify-start")

        with splitter.after:
            films_container_ref.append(ui.column())
