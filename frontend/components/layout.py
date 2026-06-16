"""Shared responsive layout classes.

Centralizes the Tailwind grid-column strings so pages don't each
hardcode their own breakpoint ladder. Tailwind breakpoints are the
framework default: sm=640px, md=768px, lg=1024px, xl=1280px.

Use:
    from frontend.components.layout import CARD_GRID_4
    with ui.grid().classes(f"{CARD_GRID_4} gap-4 w-full"):
        ...
"""

# 4-up card grid — search results, browse films, awards nominees.
CARD_GRID_4 = "grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4"

# 5-up on wide screens — detail page similar-films wants denser view.
CARD_GRID_5 = "grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"

# 2-column dimension cards — tag_grid.
TAG_GRID = "grid-cols-1 md:grid-cols-2"
