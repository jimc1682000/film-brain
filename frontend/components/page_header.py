"""Shared page header — one consistent title treatment across every feature.

Big brand-gradient title + optional subtitle (the search hero look), so all
feature pages read as one product. Uses the global .cp-gradient class defined
in app._BRAND_CSS.
"""

from nicegui import ui


def page_header(title: str, subtitle: str = "") -> None:
    with ui.column().classes("w-full items-start gap-1 q-mt-lg q-mb-md"):
        ui.label(title).classes("text-h3 cp-gradient").style(
            "font-weight: 800; letter-spacing: .5px; line-height: 1.15;"
        )
        if subtitle:
            ui.label(subtitle).classes("text-subtitle1 text-grey-5")
