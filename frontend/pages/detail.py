"""Film detail page — full info + TMDb + tags by dimension + similar films."""

import json

from nicegui import app, run, ui

from frontend.api_client import api
from frontend.components import film_list
from frontend.components.awards_section import render_awards_section
from frontend.components.badges import rating_badge
from frontend.components.messages import fatal_label
from frontend.components.poster import render_poster
from frontend.components.tag_list import (
    default_checked,
    merge_saved_and_suggestions,
    tag_grid,
)
from frontend.components.theme import dim_label
from frontend.i18n import t

_ALL_TAGS_CACHE: list[dict] = []


def _all_tags() -> list[dict]:
    """Lazy-load full taxonomy for 新增 tag dropdown."""
    if not _ALL_TAGS_CACHE:
        try:
            data = api.list_tags()
            tags = data.get("tags", []) if isinstance(data, dict) else data
            _ALL_TAGS_CACHE.extend(tags)
        except Exception:
            pass
    return _ALL_TAGS_CACHE


def _parse_json_field(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _render_tag_section(film: dict) -> None:
    """Unified tag UI — single list, checkbox-driven, status-routed save.

    View mode: read-only card grid (no checkboxes).
    Edit mode: saved + latest AI suggestions merged into one list. Checkbox
    semantics: ☑ = keep/add, ☐ = drop/skip. Route on save by tag `status`:
      - status=saved  AND ☐ → POST reject review
      - status=suggested AND ☑ → POST accept
      - manual adds via 新增標籤 → append with status=suggested, pre-checked
    """
    film_id = film["film_id"]
    section = ui.column().classes("w-full gap-3")
    # Real model name for the re-analyze button / loader (env-driven backend).
    _model_name = api.llm_info().get("primary_model", "LLM")

    # Curation-award tags are system-derived (from award_nominees) and shown
    # in the dedicated Awards section above; keep them out of the semantic
    # tag editor to avoid duplication + accidental rejection.
    saved_tags = [t for t in film.get("tags", []) if t.get("dimension") != "curation-award"]

    state: dict = {
        "saved": saved_tags,
        "suggestions": [],
        "manual_adds": [],  # list[dict] — user-picked new tags, status=suggested
        "mode": "view",
        "checked": set(),  # current checkbox set during edit
        "initial": set(),  # snapshot at edit-mode entry for visual diff
    }

    def _unified_for_edit() -> list[dict]:
        tags = merge_saved_and_suggestions(
            state["saved"], state["suggestions"] + state["manual_adds"]
        )
        return tags

    def _reset_edit_state() -> None:
        unified = _unified_for_edit()
        # Uniform rule: confidence ≥ 0.6 → pre-checked. Saved tags default
        # conf = 1.0 so they come in checked; low-conf suggestions stay off.
        preset = default_checked(unified)
        # Force-check anything already saved (defensive — saved with missing
        # confidence would otherwise drop out of preset).
        preset.update(t["tag_id"] for t in state["saved"] if t.get("tag_id"))
        state["checked"] = set(preset)
        state["initial"] = set(preset)

    def on_toggle(tag_id: str, is_checked: bool) -> None:
        if is_checked:
            state["checked"].add(tag_id)
        else:
            state["checked"].discard(tag_id)
        render()

    def on_toggle_many(tag_ids: list[str], is_checked: bool) -> None:
        if is_checked:
            state["checked"].update(tag_ids)
        else:
            state["checked"].difference_update(tag_ids)
        render()

    def render() -> None:
        section.clear()
        with section:
            with ui.row().classes("items-center gap-3 w-full"):
                ui.label(t("detail.tags_head")).classes("text-h5")
                ui.space()
                if state["mode"] == "view":
                    ui.button(t("detail.btn_edit"), on_click=enter_edit).props(
                        "outline color=primary"
                    )
                    ui.button(
                        t("detail.btn_reanalyze"),
                        on_click=lambda: reanalyze(),
                    ).props("outline color=warning").tooltip(
                        t("detail.reanalyze_tip", model=_model_name)
                    )
                    ui.button(
                        t("detail.btn_delete"),
                        on_click=lambda: confirm_delete(),
                    ).props("outline color=negative").tooltip(t("detail.delete_tip"))
                else:
                    ui.button(t("detail.btn_save"), on_click=save_edits).props(
                        "color=positive unelevated"
                    )
                    ui.button(t("common.cancel"), on_click=cancel_edits).props("flat color=grey")

            if state["mode"] == "view":
                if not state["saved"]:
                    ui.label(t("detail.no_tags")).classes("text-grey")
                    return
                view_tags = [{**t, "status": "saved"} for t in state["saved"]]
                tag_grid(view_tags, editable=False)
                return

            # Edit mode — unified list with checkboxes
            unified = _unified_for_edit()
            tag_grid(
                unified,
                editable=True,
                checked=state["checked"],
                initial=state["initial"],
                on_toggle=on_toggle,
                on_toggle_many=on_toggle_many,
            )
            _render_add_panel(unified)
            _render_diff_summary()

    def _render_add_panel(unified: list[dict]) -> None:
        ui.separator().classes("q-my-sm")
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label(t("detail.add_tag")).classes("text-subtitle2")
            current_ids = {t["tag_id"] for t in unified}
            options = {
                t["tag_id"]: f"[{dim_label(t['dimension'])}] "
                f"{t.get('label_zh_tw') or t['tag_id']} ({t.get('label_en', '')})"
                for t in _all_tags()
                if t["tag_id"] not in current_ids
            }
            select = (
                ui.select(options=options, with_input=True, label=t("detail.search_tag"))
                .classes("flex-grow")
                .props("outlined dense")
            )

            def on_add():
                tid = select.value
                if not tid:
                    return
                tag_obj = next((t for t in _all_tags() if t["tag_id"] == tid), None)
                if not tag_obj:
                    return
                state["manual_adds"].append(
                    {
                        "tag_id": tid,
                        "dimension": tag_obj["dimension"],
                        "label_zh_tw": tag_obj.get("label_zh_tw", ""),
                        "label_en": tag_obj.get("label_en", ""),
                        "confidence": 1.0,
                    }
                )
                state["checked"].add(tid)  # manual adds are implicitly chosen
                select.value = None
                render()

            ui.button(t("common.add"), on_click=on_add).props("color=primary")

    def _render_diff_summary() -> None:
        to_reject = [
            t["tag_id"]
            for t in state["saved"]
            if t.get("tag_id") in state["initial"] and t["tag_id"] not in state["checked"]
        ]
        to_accept: list[str] = []
        saved_ids = {t.get("tag_id") for t in state["saved"]}
        for tid in state["checked"]:
            if tid not in saved_ids:
                to_accept.append(tid)
        if not to_reject and not to_accept:
            ui.label(t("detail.no_changes")).classes("text-caption text-grey q-mt-sm")
            return
        with ui.row().classes("gap-4 q-mt-sm"):
            if to_reject:
                ui.badge(t("detail.diff_remove", n=len(to_reject)), color="negative").classes(
                    "text-sm"
                )
            if to_accept:
                ui.badge(t("detail.diff_add", n=len(to_accept)), color="positive").classes(
                    "text-sm"
                )

    def enter_edit() -> None:
        state["mode"] = "edit"
        _reset_edit_state()
        render()

    def cancel_edits() -> None:
        state["mode"] = "view"
        state["suggestions"] = []
        state["manual_adds"] = []
        state["checked"].clear()
        state["initial"].clear()
        render()

    async def save_edits() -> None:
        errors: list[str] = []
        saved_ids = {t.get("tag_id") for t in state["saved"]}
        rejected = 0
        for tid in state["initial"]:
            if tid in saved_ids and tid not in state["checked"]:
                try:
                    api.submit_review(film_id, tid, "rejected")
                    rejected += 1
                except Exception as e:
                    errors.append(t("detail.err_remove", tid=tid, e=e))
        to_accept = [tid for tid in state["checked"] if tid not in saved_ids]
        accepted = 0
        if to_accept:
            try:
                api.accept_tags(film_id, to_accept)
                accepted = len(to_accept)
            except Exception as e:
                errors.append(t("detail.err_add", e=e))

        if errors:
            ui.notify("; ".join(errors), type="negative")
        else:
            ui.notify(t("detail.saved", r=rejected, a=accepted), type="positive")

        try:
            refreshed = api.get_film(film_id)
            state["saved"] = [
                t for t in refreshed.get("tags", []) if t.get("dimension") != "curation-award"
            ]
        except Exception:
            pass
        state["mode"] = "view"
        state["suggestions"] = []
        state["manual_adds"] = []
        state["checked"].clear()
        state["initial"].clear()
        render()

    def confirm_delete() -> None:
        """Editor-initiated film delete. Two-step confirm because the action
        cascades to film_tags, tag_reviews, vector and unlinks award nominees."""
        with ui.dialog() as dialog, ui.card():
            ui.label(t("detail.del_confirm", title=film.get("title_zh", film_id))).classes(
                "text-h6"
            )
            ui.label(t("detail.del_warn")).classes("text-caption text-grey")
            ui.label(t("detail.del_irreversible")).classes("text-negative")
            with ui.row().classes("justify-end gap-2 w-full"):
                ui.button(t("common.cancel"), on_click=dialog.close).props("flat color=grey")
                ui.button(
                    t("detail.del_btn"),
                    on_click=lambda: _do_delete(dialog),
                ).props("color=negative unelevated")
        dialog.open()

    def _do_delete(dialog) -> None:
        try:
            result = api.delete_film(film_id)
        except Exception as e:
            ui.notify(t("detail.del_failed", e=e), type="negative")
            return
        dialog.close()
        ui.notify(
            t(
                "detail.deleted",
                tags=result.get("tags_deleted"),
                reviews=result.get("reviews_deleted"),
                unlinked=result.get("nominees_unlinked"),
            ),
            type="positive",
        )
        ui.navigate.to("/browse")

    async def reanalyze() -> None:
        from nicegui import run

        from frontend.components.loading_dialog import blocking_loader

        try:
            async with blocking_loader(
                t("detail.reanalyzing"),
                t("detail.reanalyzing_sub", model=_model_name),
            ):
                data = await run.io_bound(api.auto_tag, film_id, "zh_TW")
            state["suggestions"] = data.get("suggestions", [])
            state["mode"] = "edit"
            _reset_edit_state()
            ui.notify(
                t(
                    "detail.got_suggestions",
                    n=len(state["suggestions"]),
                    model=data.get("model_used"),
                ),
                type="positive",
            )
        except Exception as e:
            ui.notify(t("detail.analyze_failed", e=e), type="negative")
            return
        render()

    render()


_HERO_CSS = """
<style>
  /* Full-bleed cinematic hero (MUBI-style). Breaks out of the centered
     max-w-6xl column via the 100vw trick; the inner body re-centers. */
  .fh { position:relative; width:100vw; margin-left:calc(50% - 50vw);
    min-height:54vh; display:flex; align-items:flex-end; overflow:hidden; }
  .fh-bg { position:absolute; inset:0; background-size:cover; background-position:center 30%; z-index:0; }
  /* No wide backdrop yet → blow up + blur the poster so low-res never shows */
  .fh-bg.blur { filter:blur(30px) brightness(.5); transform:scale(1.15); }
  .fh-grad { position:absolute; inset:0; z-index:1; background:linear-gradient(0deg,
    rgba(0,0,0,.94) 0%, rgba(0,0,0,.6) 40%, rgba(0,0,0,.2) 72%, rgba(0,0,0,.45) 100%); }
  .fh-body { position:relative; z-index:2; width:100%; max-width:1152px; margin:0 auto;
    padding:40px 24px 30px; display:flex; gap:26px; align-items:flex-end; }
  .fh-poster { flex:0 0 auto; width:176px; border-radius:10px; overflow:hidden;
    box-shadow:0 14px 44px rgba(0,0,0,.65); border:1px solid rgba(255,255,255,.12); }
  .fh-poster img { width:100%; display:block; }
  .fh-info { flex:1 1 auto; min-width:0; }
  .fh-title { font-size:2.4rem; font-weight:800; line-height:1.1; letter-spacing:.3px; }
  .fh-en { color:#b8b8b8; font-size:1.05rem; margin-top:3px; }
  .fh-meta { color:#d2d2d2; font-size:.92rem; margin-top:12px; display:flex; gap:10px;
    flex-wrap:wrap; align-items:center; }
  .fh-syn { color:#dadada; font-size:.93rem; margin-top:13px; max-width:680px; line-height:1.55;
    display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
  .fh-cta { margin-top:18px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  @media (max-width:640px) {
    .fh-body { flex-direction:column; align-items:flex-start; padding:26px 16px 22px; }
    .fh-title { font-size:1.7rem; } .fh-poster { width:124px; }
  }
</style>
"""


def _hero_head(film: dict) -> None:
    """MUBI-style cinematic hero. Uses a wide backdrop when available, else a
    heavily blurred blow-up of the poster (so the w500 art never looks pixelated
    stretched full-bleed), with the sharp poster as the focal point on top."""
    ui.add_head_html(_HERO_CSS)
    poster = film.get("poster_url") or ""
    backdrop = film.get("tmdb_backdrop_url") or ""
    bg_cls = "fh-bg" if backdrop else "fh-bg blur"
    bg_url = backdrop or poster

    with ui.element("div").classes("fh"):
        if bg_url:
            ui.element("div").classes(bg_cls).style(f"background-image:url('{bg_url}')")
        ui.element("div").classes("fh-grad")
        with ui.element("div").classes("fh-body"):
            if poster:
                with ui.element("div").classes("fh-poster"):
                    ui.image(poster)
            with ui.element("div").classes("fh-info"):
                ui.label(film.get("title_zh", "")).classes("fh-title")
                if film.get("title_en"):
                    ui.label(film["title_en"]).classes("fh-en")
                # Hero stays minimal: title + rating only. Director / cast /
                # genre are grouped together in the details block below.
                with ui.element("div").classes("fh-meta"):
                    if film.get("tmdb_vote_avg"):
                        rating_badge(film["tmdb_vote_avg"], prefix="TMDb ⭐")
                # Synopsis intentionally omitted here — the full 劇情介紹 +
                # TMDb overview live in the details block below (no duplication).
                with ui.element("div").classes("fh-cta"):
                    if film.get("catchplay_url"):
                        ui.button(
                            t("detail.watch_cp"),
                            on_click=lambda: ui.navigate.to(film["catchplay_url"], new_tab=True),
                        ).props("unelevated color=primary no-caps").classes("rounded")
                    if film.get("tmdb_id"):
                        ui.link(
                            f"TMDb #{film['tmdb_id']}",
                            f"https://www.themoviedb.org/movie/{film['tmdb_id']}",
                            new_tab=True,
                        ).classes("text-caption text-grey")


def _details_block(film: dict) -> None:
    """Secondary info shown below the hero (kept out of the hero to stay clean):
    full plot, TMDb overview, genres/keywords, cast."""
    with ui.column().classes("w-full gap-2 q-mt-lg"):
        # Director / cast / genre grouped together as one credit block.
        with ui.column().classes("gap-1"):
            if film.get("tmdb_director"):
                ui.label(t("detail.director", v=film["tmdb_director"])).classes(
                    "text-caption text-grey"
                )
            cast = _parse_json_field(film.get("tmdb_cast"))
            if cast:
                ui.label(t("detail.cast", v=", ".join(cast[:5]))).classes("text-caption text-grey")
            if film.get("original_genre"):
                ui.label(t("detail.genre", v=film["original_genre"])).classes(
                    "text-caption text-grey"
                )

        desc = film.get("description") or film.get("description_raw")
        if desc:
            with ui.row().classes("items-baseline gap-2"):
                ui.label(t("detail.plot")).classes("text-subtitle1 font-bold")
                ui.badge(t("detail.src_cp"), color="primary").props("outline").classes("text-xs")
            ui.label(desc).classes("text-body2")

        overview = film.get("tmdb_overview")
        if overview and overview != desc:
            with ui.row().classes("items-baseline gap-2 q-mt-sm"):
                ui.label(t("detail.tmdb_overview")).classes("text-subtitle1 font-bold")
                ui.badge(t("common.source_tmdb"), color="orange").props("outline").classes(
                    "text-xs"
                )
            ui.label(overview).classes("text-body2 text-grey-8")

        genres = _parse_json_field(film.get("tmdb_genres"))
        keywords = _parse_json_field(film.get("tmdb_keywords"))
        if genres or keywords:
            with ui.row().classes("items-baseline gap-2 q-mt-sm"):
                ui.label(t("detail.tmdb_genres_kw")).classes("text-subtitle2 font-bold")
                ui.badge(t("common.source_tmdb"), color="orange").props("outline").classes(
                    "text-xs"
                )
            with ui.row().classes("gap-1 flex-wrap"):
                for g in genres:
                    ui.badge(g, color="blue-grey").props("outline").classes("text-xs")
                for k in keywords[:10]:
                    ui.badge(k, color="grey").props("outline").classes("text-xs")


def _render_rest(film_id: str, film: dict) -> None:
    """Shared tail for both head layouts: awards, tags, similar films."""
    # Awards this film appears in
    ui.separator().classes("q-my-lg")
    render_awards_section(film_id, film)

    # Tags by dimension — view/edit mode toggle
    ui.separator().classes("q-my-lg")
    _render_tag_section(film)

    # Similar films — deferred load with a spinner so the rest of the detail
    # page paints immediately (the lookup is fast, but the cold-cache fallback
    # path can take a moment).
    ui.separator().classes("q-my-lg")
    ui.label(t("detail.similar")).classes("text-h5 q-mb-md")
    sim_container = ui.column().classes("w-full")
    with sim_container:
        ui.spinner(size="lg", color="primary")

    async def _load_similar():
        try:
            sim = await run.io_bound(api.similar_films, film_id, 6)
            # Drop near-zero matches (<10%) — they're noise, not "similar".
            sim_results = [r for r in sim.get("results", []) if r.get("score", 0) >= 0.10]
        except Exception as e:
            try:
                sim_container.clear()
                with sim_container:
                    ui.label(t("detail.similar_failed", e=e)).classes("text-grey text-caption")
            except RuntimeError:
                pass
            return
        try:
            sim_container.clear()
            with sim_container:
                if sim_results:
                    film_list.render(sim_results, app.storage.user.get("style", "default"))
                else:
                    ui.label(t("detail.no_similar")).classes("text-grey")
        except RuntimeError:
            return  # navigated away before similar films loaded — slot gone

    ui.timer(0.1, _load_similar, once=True)


def _classic_head(film: dict) -> None:
    """Default-style head: poster left, info right."""
    with ui.row().classes("w-full gap-6 flex-wrap md:flex-nowrap"):
        # Left — poster (detail page uses a larger fixed-width aspect ratio
        # than the card grid; pass custom classes to render_poster so the
        # placeholder branch matches).
        with ui.column().classes("items-center gap-3 w-full md:w-auto"):
            render_poster(
                film.get("poster_url"),
                classes="w-40 sm:w-56 md:w-64 rounded shadow",
                placeholder_classes=(
                    "w-40 sm:w-56 md:w-64 h-60 sm:h-80 md:h-96 "
                    "bg-grey-3 rounded flex items-center justify-center"
                ),
            )

            if film.get("catchplay_url"):
                ui.link(t("detail.watch_cp"), film["catchplay_url"], new_tab=True).classes(
                    "text-primary"
                )

            tmdb_id = film.get("tmdb_id")
            if tmdb_id:
                ui.link(
                    f"TMDb #{tmdb_id}", f"https://www.themoviedb.org/movie/{tmdb_id}", new_tab=True
                ).classes("text-caption text-grey")

        # Right — info
        with ui.column().classes("flex-grow gap-2"):
            ui.label(film.get("title_zh", "")).classes("text-h4 font-bold")
            en = film.get("title_en")
            if en:
                ui.label(en).classes("text-h6 text-grey")

            with ui.row().classes("gap-2 items-center q-mt-sm"):
                if film.get("original_genre"):
                    ui.badge(film["original_genre"], color="grey-7").classes("text-sm")
                if film.get("tmdb_vote_avg"):
                    rating_badge(film["tmdb_vote_avg"], prefix="TMDb ⭐")
                if film.get("tmdb_director"):
                    ui.label(t("detail.director", v=film["tmdb_director"])).classes(
                        "text-caption text-grey"
                    )

            cast = _parse_json_field(film.get("tmdb_cast"))
            if cast:
                ui.label(t("detail.cast", v=", ".join(cast[:5]))).classes("text-caption text-grey")

            desc = film.get("description") or film.get("description_raw")
            if desc:
                ui.separator().classes("q-my-sm")
                with ui.row().classes("items-baseline gap-2"):
                    ui.label(t("detail.plot")).classes("text-subtitle1 font-bold")
                    ui.badge(t("detail.src_cp"), color="primary").props("outline").classes(
                        "text-xs"
                    )
                ui.label(desc).classes("text-body2")

            overview = film.get("tmdb_overview")
            if overview and overview != desc:
                with ui.row().classes("items-baseline gap-2 q-mt-sm"):
                    ui.label(t("detail.tmdb_overview")).classes("text-subtitle1 font-bold")
                    ui.badge(t("common.source_tmdb"), color="orange").props("outline").classes(
                        "text-xs"
                    )
                ui.label(overview).classes("text-body2 text-grey-8")

            genres = _parse_json_field(film.get("tmdb_genres"))
            keywords = _parse_json_field(film.get("tmdb_keywords"))
            if genres or keywords:
                with ui.row().classes("items-baseline gap-2 q-mt-sm"):
                    ui.label(t("detail.tmdb_genres_kw")).classes("text-subtitle2 font-bold")
                    ui.badge(t("common.source_tmdb"), color="orange").props("outline").classes(
                        "text-xs"
                    )
                with ui.row().classes("gap-1 flex-wrap"):
                    for g in genres:
                        ui.badge(g, color="blue-grey").props("outline").classes("text-xs")
                    for k in keywords[:10]:
                        ui.badge(k, color="grey").props("outline").classes("text-xs")


def detail_page(film_id: str):
    """Render full film detail."""
    wrap = "q-pa-lg w-full max-w-6xl mx-auto"
    try:
        film = api.get_film(film_id)
    except Exception as e:
        with ui.column().classes(wrap):
            fatal_label(t("detail.load_failed", e=e))
            ui.link(t("detail.back_search"), "/")
        return

    if not film or "film_id" not in film:
        with ui.column().classes(wrap):
            fatal_label(t("detail.not_found"))
            ui.link(t("detail.back_search"), "/")
        return

    # Non-default style → full-bleed cinematic hero + centered details below;
    # default keeps the classic centered layout.
    if app.storage.user.get("style", "default") != "default":
        _hero_head(film)
        with ui.column().classes("q-pa-lg w-full max-w-6xl mx-auto"):
            _details_block(film)
            _render_rest(film_id, film)
        return

    with ui.column().classes("q-pa-lg w-full max-w-6xl mx-auto"):
        _classic_head(film)
        _render_rest(film_id, film)
