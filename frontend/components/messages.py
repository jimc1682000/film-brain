"""Reusable status / error / empty-state labels.

Every page rendered its own one-liner for "load failed", "no results",
"film not found" etc. Same Quasar classes, slightly different copy. This
module centralises them so we change the visual once when palette
tweaks come.
"""

from nicegui import ui


def error_label(message: str):
    """Inline error message — red, body size."""
    return ui.label(message).classes("text-negative text-body1")


def fatal_label(message: str):
    """Block-level error (used on detail page when the resource is missing)."""
    return ui.label(message).classes("text-negative text-h6")


def hint_label(message: str):
    """Muted informational note (e.g. 'no results yet')."""
    return ui.label(message).classes("text-caption text-grey")
