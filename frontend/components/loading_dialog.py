"""Modal loading dialog — stays open until the awaited task completes."""

from collections.abc import Callable
from contextlib import asynccontextmanager

from nicegui import ui

from frontend.i18n import t


@asynccontextmanager
async def blocking_loader(message: str, sub: str = "", on_cancel: Callable[[], None] | None = None):
    """Show a modal spinner + message; auto-close on context exit.

    NiceGUI `ui.notify` auto-dismisses after ~5s regardless of async state —
    use this when the wait is expected to exceed that (LLM calls, enrichment).
    The dialog has `persistent` so users cannot click away while work runs.

    Pass `on_cancel` to render a 取消 button — useful when the wait is a
    long LLM call the user may want to abandon. Cancelling unblocks the UI;
    note the in-flight backend request still completes server-side.
    """
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().classes("min-w-[280px]"):
        with ui.row().classes("items-center gap-4 no-wrap"):
            ui.spinner(size="lg", color="primary")
            with ui.column().classes("gap-1"):
                ui.label(message).classes("text-subtitle1")
                if sub:
                    ui.label(sub).classes("text-caption text-grey")
        if on_cancel is not None:
            with ui.row().classes("justify-end w-full q-mt-sm"):
                ui.button(t("common.cancel"), on_click=on_cancel, icon="close").props(
                    "flat color=negative"
                )
    dialog.open()
    try:
        yield dialog
    finally:
        dialog.close()
