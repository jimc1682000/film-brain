"""Score / rating badge helpers.

The codebase had three subtly-different colour codings for the TMDB
vote_avg badge (amber, amber, orange) and two different threshold sets
for the search-score badge. This module collapses them into a couple of
named factories so all callers agree.
"""

from nicegui import ui

from frontend.components.theme import score_color


def score_badge(value: float, *, classes: str = "self-start", outline: bool = False):
    """Percent-formatted 0-1 score with shared threshold colour."""
    badge = ui.badge(f"{value:.0%}", color=score_color(value)).classes(classes)
    if outline:
        badge.props("outline")
    return badge


def rating_badge(value: float, *, prefix: str = "★"):
    """TMDB vote_avg style badge — 0-10 float, fixed brand-warning colour.

    Rating spread is too narrow on TMDB to map onto our 3-tier thresholds
    sensibly, so we keep one stable accent. Using the `warning` palette
    slot (catchplay yellow #f2a93b) gives the same visual everywhere
    instead of the amber / orange split we had.
    """
    return ui.badge(f"{prefix} {value:.1f}", color="warning").props("outline").classes("text-xs")
