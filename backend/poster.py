"""Title-card poster generation.

A self-contained placeholder poster as an SVG data URI — no network, no asset
files, deterministic. Used for films with no real artwork (the synthetic
dataset, or a real film not yet enriched) so cards show a coloured tile with the
film title instead of a broken <img> or a generic brand logo.
"""

from __future__ import annotations

import hashlib
from urllib.parse import quote
from xml.sax.saxutils import escape


def title_card_data_uri(title: str, seed_key: str = "") -> str:
    """Return a `data:image/svg+xml` poster: the title centered on a coloured
    tile. Hue is seeded from `seed_key` (e.g. film_id) so each film looks
    distinct; falls back to the title. The `viewBox` is required — without it an
    <img> with `object-fit: cover` mis-scales and clips the <text> out (the rect
    still fills, so you'd see a blank colour). The title is XML-escaped."""
    # md5 is a non-cryptographic hue seed here (just want a stable spread of
    # colours per film), so usedforsecurity=False — silences the bandit S324.
    digest = hashlib.md5((seed_key or title).encode(), usedforsecurity=False).hexdigest()
    hue = int(digest[:2], 16) * 360 // 256
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 450" '
        'width="300" height="450">'
        f'<rect width="100%" height="100%" fill="hsl({hue},45%,28%)"/>'
        '<text x="50%" y="50%" fill="#f0f0f0" font-family="sans-serif" font-size="26" '
        'text-anchor="middle" dominant-baseline="middle">'
        f"{escape(title)}</text></svg>"
    )
    return "data:image/svg+xml;utf8," + quote(svg)
