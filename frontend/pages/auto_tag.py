"""Page 2 — Auto-Tag Demo."""

import asyncio
import json

from nicegui import run, ui

from frontend.api_client import api
from frontend.components.badges import rating_badge
from frontend.components.loading_dialog import blocking_loader
from frontend.components.page_header import page_header
from frontend.components.tag_list import default_checked, tag_grid
from frontend.i18n import t


def auto_tag_page():
    """Build the auto-tag demo page.

    Layout order matters: title + tab form render first (stay at the top),
    then status_label + results_container are appended *below* so analysis
    output grows downward instead of pushing the form to the bottom. The
    two are created at the end of this function; the handler closures above
    resolve them at click-time, so forward reference is fine.
    """
    # Created at the END of the layout so results render below the form.
    # None until then; handlers fire only after build, except the tab
    # on_value_change which can fire during init — _reset_results guards.
    results_container = None
    status_label = None
    # id=None means "preview mode" (new-film input, not yet in DB). `preview`
    # holds the form/enrich data so 建立影片 can persist it after analysis.
    current_film: dict = {"id": None, "suggestions": [], "preview": None}
    # Read the live LLM config so the loading text names the model that
    # actually runs (backend/fallback are env-driven, not hard-coded).
    _llm = api.llm_info()
    _model_name = _llm.get("primary_model", "LLM")

    def load_films():
        # Load the whole library — the select is searchable (with_input), so a
        # few hundred options are fine, and the prior limit=100 hid ~85% of the
        # catalogue once it grew past 600 films.
        try:
            data = api.list_films(limit=5000)
            films = data.get("films", [])
            return {f["film_id"]: f["title_zh"] for f in films}
        except Exception:
            return {}

    def go_to_detail(film_id: str | None):
        # Existing films already have a full detail page with re-analyze /
        # edit / delete — jump there instead of duplicating the flow inline.
        if film_id:
            ui.navigate.to(f"/film/{film_id}")

    async def run_preview():
        title_zh = title_zh_input.value.strip()
        if not title_zh:
            ui.notify(t("autotag.need_title"), type="warning")
            return
        enrich = bool(enrich_toggle.value)
        payload = {
            "title_zh": title_zh,
            "title_en": title_en_input.value.strip() or None,
            "description": desc_input.value.strip() or None,
            "original_genre": genre_input.value.strip() or None,
            "locale": "zh_TW",
            "enrich": enrich,
        }
        _lock_buttons()
        results_container.clear()
        status_label.text = t("autotag.analyzing_status", model=_model_name)
        task: asyncio.Task | None = None

        def _cancel():
            if task is not None:
                task.cancel()

        try:
            sub = t("autotag.sub_enrich") if enrich else t("autotag.sub_plain")
            async with blocking_loader(
                t("autotag.analyzing", model=_model_name), sub, on_cancel=_cancel
            ):
                task = asyncio.create_task(run.io_bound(api.auto_tag_preview, payload))
                data = await task
            current_film["id"] = None
            current_film["preview"] = {**payload, "enriched": data.get("enriched_film") or {}}
            _render_after_analysis(data)
        except asyncio.CancelledError:
            # UI-abandon: the in-flight backend request still completes
            # server-side, but the user is unblocked and the result discarded.
            results_container.clear()
            status_label.text = t("autotag.cancelled")
            ui.notify(t("autotag.cancelled_toast"), type="info")
        except Exception as e:
            _render_error(e)
        finally:
            _unlock_buttons()

    def _lock_buttons():
        preview_btn.disable()
        reanalyze_preview_btn.disable()

    def _unlock_buttons():
        preview_btn.enable()
        reanalyze_preview_btn.enable()

    def _render_after_analysis(data: dict):
        current_film["suggestions"] = data.get("suggestions", [])
        model = data.get("model_used", "unknown")
        status_label.text = t("autotag.done", n=len(current_film["suggestions"]), model=model)
        # Cloud-throttled fallback warning (set by backend when Gemini 429'd).
        warning = data.get("warning")
        if warning:
            ui.notify(warning, type="warning", timeout=8000)
        results_container.clear()
        if warning:
            with results_container:
                with (
                    ui.row()
                    .classes("items-center gap-2 q-pa-sm rounded w-full")
                    .style("border: 1px solid #f2a93b; background: rgba(242,169,59,0.12);")
                ):
                    ui.icon("warning").classes("text-warning")
                    ui.label(warning).classes("text-caption text-warning")
        enriched = data.get("enriched_film")
        if enriched:
            _render_enriched_block(enriched)
        _render_suggestions(current_film["suggestions"])
        reanalyze_preview_btn.set_visibility(True)

    def _render_enriched_block(enriched: dict):
        """Show TMDB fields filled in by the server during preview enrich step."""

        def _parse_list(raw) -> list[str]:
            if isinstance(raw, list):
                return raw
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    return parsed if isinstance(parsed, list) else [raw]
                except json.JSONDecodeError:
                    return [raw]
            return []

        # Dark surface (inherits #1f1f1f from app CSS) — the old bg-blue-1
        # light tile made the dark-mode light text invisible. A blue left
        # border keeps the "TMDB enriched" visual cue.
        with (
            results_container,
            ui.card().classes("w-full q-mb-md").style("border-left: 4px solid #00a3d9;"),
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("auto_awesome").classes("text-info")
                ui.label(t("autotag.enriched_title")).classes("text-subtitle2 font-bold")
                if enriched.get("tmdb_id"):
                    mt = enriched.get("tmdb_media_type", "movie")
                    ui.badge(f"TMDb #{enriched['tmdb_id']} ({mt})", color="blue").props("outline")
                if enriched.get("tmdb_vote_avg") is not None:
                    rating_badge(enriched["tmdb_vote_avg"], prefix=t("autotag.rating"))

            if enriched.get("tmdb_overview"):
                ui.label(t("autotag.f_overview")).classes("text-caption text-grey q-mt-xs")
                ui.label(enriched["tmdb_overview"]).classes("text-body2")

            genres = _parse_list(enriched.get("tmdb_genres"))
            if genres:
                ui.label(t("autotag.f_genres")).classes("text-caption text-grey q-mt-xs")
                with ui.row().classes("gap-1 flex-wrap"):
                    for g in genres:
                        ui.badge(g, color="teal").classes("text-xs")

            keywords = _parse_list(enriched.get("tmdb_keywords"))
            if keywords:
                ui.label(t("autotag.f_keywords")).classes("text-caption text-grey q-mt-xs")
                with ui.row().classes("gap-1 flex-wrap"):
                    for k in keywords[:20]:
                        ui.badge(k, color="purple").classes("text-xs")

            cast = _parse_list(enriched.get("tmdb_cast"))
            if cast:
                ui.label(t("autotag.f_cast")).classes("text-caption text-grey q-mt-xs")
                ui.label(", ".join(cast)).classes("text-body2")

            if enriched.get("tmdb_director"):
                ui.label(t("autotag.f_director")).classes("text-caption text-grey q-mt-xs")
                ui.label(enriched["tmdb_director"]).classes("text-body2")

    def _render_error(e: Exception):
        error_msg = str(e)
        results_container.clear()
        if "503" in error_msg:
            status_label.text = t("autotag.err_503")
        else:
            status_label.text = t("autotag.err_generic", msg=error_msg)
        ui.notify(str(e), type="negative")

    checked: set[str] = set()

    def _render_suggestions(suggestions: list[dict]):
        """Render suggestions via unified tag_grid. All rows have status=suggested."""
        tags = [{**s, "status": "suggested"} for s in suggestions]
        preset = default_checked(tags)
        checked.clear()
        checked.update(preset)
        initial = set(preset)

        with results_container:
            suggestions_area = ui.column().classes("w-full")

        def on_toggle(tag_id: str, is_checked: bool) -> None:
            if is_checked:
                checked.add(tag_id)
            else:
                checked.discard(tag_id)

        def on_toggle_many(tag_ids: list[str], is_checked: bool) -> None:
            if is_checked:
                checked.update(tag_ids)
            else:
                checked.difference_update(tag_ids)
            _paint()

        def _paint():
            suggestions_area.clear()
            with suggestions_area:
                tag_grid(
                    tags,
                    editable=True,
                    checked=checked,
                    initial=initial,
                    on_toggle=on_toggle,
                    on_toggle_many=on_toggle_many,
                )
                with ui.row().classes("q-mt-lg gap-4 items-center"):
                    ui.button(
                        t("autotag.btn_create"),
                        on_click=lambda: _create_new_film(),
                        icon="library_add",
                    ).props("color=positive").tooltip(t("autotag.create_tip"))

        _paint()

    async def _create_new_film():
        preview = current_film.get("preview")
        if not preview or not current_film["suggestions"]:
            return
        catchplay_url = catchplay_input.value.strip() or None
        if not checked:
            ui.notify(t("autotag.need_tag"), type="warning")
            return
        enriched = preview.get("enriched") or {}
        tags = [
            {
                "tag_id": s["tag_id"],
                "confidence": s.get("confidence", 0.5),
                "reasoning": s.get("reasoning", ""),
            }
            for s in current_film["suggestions"]
            if s["tag_id"] in checked
        ]
        payload = {
            "catchplay_url": catchplay_url,
            "title_zh": preview["title_zh"],
            "title_en": preview.get("title_en"),
            "description": preview.get("description"),
            "original_genre": preview.get("original_genre"),
            "poster_url": poster_input.value.strip() or None,
            "tmdb_poster_url": enriched.get("tmdb_poster_url"),
            "tmdb_id": enriched.get("tmdb_id"),
            "tmdb_overview": enriched.get("tmdb_overview"),
            "tmdb_genres": enriched.get("tmdb_genres"),
            "tmdb_keywords": enriched.get("tmdb_keywords"),
            "tmdb_vote_avg": enriched.get("tmdb_vote_avg"),
            "tmdb_cast": enriched.get("tmdb_cast"),
            "tmdb_director": enriched.get("tmdb_director"),
            "tags": tags,
        }
        try:
            res = await run.io_bound(api.create_film, payload)
            embedded = (
                t("autotag.embedded_yes") if res.get("embedded") else t("autotag.embedded_no")
            )
            ui.notify(
                t(
                    "autotag.created",
                    film_id=res["film_id"],
                    n=res["saved_tags"],
                    embedded=embedded,
                ),
                type="positive",
                timeout=8000,
            )
        except Exception as e:
            msg = str(e)
            if "409" in msg:
                ui.notify(t("autotag.exists"), type="warning")
            else:
                ui.notify(msg, type="negative")

    reanalyze_preview_btn = None

    def _reset_results():
        """Clear UI state when the user switches between existing / preview tabs."""
        if results_container is None or status_label is None:
            return  # tab on_value_change can fire during initial build
        results_container.clear()
        status_label.text = ""
        current_film["id"] = None
        current_film["suggestions"] = []
        current_film["preview"] = None
        checked.clear()
        if reanalyze_preview_btn is not None:
            reanalyze_preview_btn.set_visibility(False)

    # --- Layout ---
    page_header(t("autotag.title"), t("autotag.desc"))

    with ui.tabs().classes("w-full").on_value_change(lambda _: _reset_results()) as tabs:
        existing_tab = ui.tab(t("autotag.tab_existing"), icon="movie")
        preview_tab = ui.tab(t("autotag.tab_new"), icon="add_circle")

    films_map = load_films()

    with ui.tab_panels(tabs, value=existing_tab).classes("w-full"):
        with ui.tab_panel(existing_tab):
            ui.label(t("autotag.existing_hint")).classes("text-caption text-grey q-mb-sm")
            ui.select(
                options=films_map,
                label=t("autotag.select_film"),
                with_input=True,
                on_change=lambda e: go_to_detail(e.value),
            ).classes("w-full").props("outlined")

        with ui.tab_panel(preview_tab):
            ui.label(t("autotag.new_hint")).classes("text-caption text-grey q-mb-sm")
            with ui.column().classes("w-full gap-2"):
                with ui.row().classes("w-full gap-3"):
                    title_zh_input = (
                        ui.input(
                            label=t("autotag.f_title_zh"), placeholder=t("autotag.f_title_zh_ph")
                        )
                        .classes("flex-grow")
                        .props("outlined dense")
                    )
                    title_en_input = (
                        ui.input(label=t("autotag.f_title_en"))
                        .classes("flex-grow")
                        .props("outlined dense")
                    )
                genre_input = (
                    ui.input(label=t("autotag.f_genre"), placeholder=t("autotag.f_genre_ph"))
                    .classes("w-full")
                    .props("outlined dense")
                )
                catchplay_input = (
                    ui.input(
                        label=t("autotag.f_catchplay"),
                        placeholder=t("autotag.f_catchplay_ph"),
                    )
                    .classes("w-full")
                    .props("outlined dense")
                )
                poster_input = (
                    ui.input(
                        label=t("autotag.f_poster"),
                        placeholder=t("autotag.f_poster_ph"),
                    )
                    .classes("w-full")
                    .props("outlined dense")
                )
                desc_input = (
                    ui.textarea(label=t("autotag.f_desc"), placeholder=t("autotag.f_desc_ph"))
                    .classes("w-full")
                    .props("outlined")
                )
                with ui.row().classes("items-center gap-2 q-mt-xs"):
                    enrich_toggle = ui.checkbox(t("autotag.enrich_toggle"), value=True)
                    ui.icon("info").classes("text-grey").tooltip(t("autotag.enrich_tip"))
                with ui.row().classes("q-mt-sm gap-3"):
                    preview_btn = ui.button(
                        t("autotag.btn_analyze_new"),
                        on_click=lambda: run_preview(),
                        icon="auto_fix_high",
                    ).props("color=primary")
                    reanalyze_preview_btn = (
                        ui.button(
                            t("autotag.btn_reanalyze"),
                            on_click=lambda: run_preview(),
                            icon="refresh",
                        )
                        .props("color=warning outline")
                        .tooltip(t("autotag.reanalyze_tip", model=_model_name))
                    )
                    reanalyze_preview_btn.set_visibility(False)

    # Results render BELOW the form so the input stays at the top of the page.
    ui.separator().classes("q-my-md")
    status_label = ui.label("").classes("text-grey")
    results_container = ui.column().classes("w-full")
