"""Feedback wiki page — Karpathy LLM-wiki style.

Layout: left sidebar = filter + page index; main = markdown render + re-analyze prompt.
Lifecycle (open/done/dismissed/merged) is LLM-managed via natural-language re-analyze —
no fixed action buttons per user design feedback.
"""

from nicegui import run, ui

from frontend.api_client import api
from frontend.components.loading_dialog import blocking_loader
from frontend.i18n import t

STATUS_COLORS = {
    "open": "primary",
    "done": "positive",
    "dismissed": "grey",
    "merged": "warning",
}


def feedback_page():
    state = {"status": "open", "selected": None, "pages": []}

    ui.label(t("feedback.title")).classes("text-h4 q-mb-sm")
    ui.label(t("feedback.desc")).classes("text-grey q-mb-md")

    with ui.splitter(value=28).classes("w-full min-h-[70vh]") as splitter:
        with splitter.before, ui.column().classes("q-pa-sm w-full gap-3"):
            status_select = ui.select(
                options={
                    "open": t("feedback.status_open"),
                    "done": t("feedback.status_done"),
                    "dismissed": t("feedback.status_dismissed"),
                    "merged": t("feedback.status_merged"),
                    "all": t("feedback.status_all"),
                },
                value="open",
                label=t("feedback.filter"),
            ).classes("w-full")
            index_col = ui.column().classes("w-full gap-1")
        with splitter.after, ui.column().classes("q-pa-md w-full"):
            main_col = ui.column().classes("w-full gap-3")

    def render_index():
        index_col.clear()
        pages = state["pages"]
        if not pages:
            with index_col:
                ui.label(t("feedback.no_pages")).classes("text-grey text-caption")
            return
        by_kind: dict[str, list[dict]] = {}
        for p in pages:
            by_kind.setdefault(p["kind"], []).append(p)
        for kind in sorted(by_kind):
            with index_col:
                ui.label(f"📚 {kind} ({len(by_kind[kind])})").classes(
                    "text-caption text-grey q-mt-sm"
                )
            for p in sorted(by_kind[kind], key=lambda x: x["page_id"]):
                with index_col, ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.badge(p["status"], color=STATUS_COLORS.get(p["status"], "grey")).props(
                        "rounded"
                    )
                    btn = (
                        ui.button(p["title"])
                        .props("flat dense no-caps align=left")
                        .classes("text-left flex-grow")
                    )
                    btn.on("click", lambda _, pid=p["page_id"]: load_page(pid))

    def load_pages():
        try:
            status = state["status"]
            state["pages"] = api.list_feedback_pages(status=None if status == "all" else status)
        except Exception as e:
            state["pages"] = []
            ui.notify(t("feedback.load_failed", e=e), type="negative")
        render_index()

    def render_page(page: dict):
        main_col.clear()
        with main_col:
            with ui.row().classes("items-center gap-2 w-full"):
                ui.label(page["title"]).classes("text-h5")
                ui.badge(page["kind"], color="secondary").props("rounded")
                ui.badge(page["status"], color=STATUS_COLORS.get(page["status"], "grey")).props(
                    "rounded"
                )
                if page.get("consultant_validated"):
                    ui.badge(t("feedback.validated"), color="positive").props("rounded")
            meta_bits = []
            if page.get("updated_at"):
                meta_bits.append(t("feedback.meta_updated", v=page["updated_at"]))
            if page.get("model_used"):
                meta_bits.append(t("feedback.meta_model", v=page["model_used"]))
            if page.get("resolution_note"):
                meta_bits.append(t("feedback.meta_note", v=page["resolution_note"]))
            if meta_bits:
                ui.label(" · ".join(meta_bits)).classes("text-caption text-grey")

            ui.separator()
            ui.markdown(page.get("body") or t("feedback.empty_body"))
            ui.separator()

            ui.label(t("feedback.reanalyze_head")).classes("text-subtitle1 q-mt-md")
            ui.label(t("feedback.reanalyze_hint")).classes("text-caption text-grey")
            prompt_input = (
                ui.textarea(placeholder=t("feedback.prompt_ph")).classes("w-full").props("autogrow")
            )

            async def do_reanalyze():
                async with blocking_loader(
                    t("feedback.reanalyzing"),
                    sub=t("feedback.reanalyzing_sub"),
                ):
                    try:
                        result = await run.io_bound(
                            api.reanalyze_feedback,
                            page["page_id"],
                            prompt_input.value or "",
                            True,
                        )
                    except Exception as e:
                        ui.notify(t("feedback.reanalyze_failed", e=e), type="negative")
                        return
                ui.notify(
                    t(
                        "feedback.updated",
                        status=result["page"]["status"],
                        model=result.get("model_used", ""),
                    ),
                    type="positive",
                )
                state["selected"] = page["page_id"]
                load_pages()
                fresh = result.get("page") or api.get_feedback_page(page["page_id"])
                render_page(fresh)

            ui.button(t("feedback.btn_reanalyze"), icon="refresh", on_click=do_reanalyze).props(
                "color=primary"
            )

    def load_page(page_id: str):
        try:
            page = api.get_feedback_page(page_id)
        except Exception as e:
            ui.notify(t("feedback.load_page_failed", e=e), type="negative")
            return
        state["selected"] = page_id
        render_page(page)

    def on_status_change(e):
        state["status"] = e.value
        load_pages()

    status_select.on_value_change(on_status_change)

    with main_col:
        ui.label(t("feedback.pick_left")).classes("text-grey")

    load_pages()
