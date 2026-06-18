"""Unit tests for the title-card poster helper.

These assert the *generated SVG markup* statically (no browser) so the
render-critical bits can't regress silently. The motivating bug: an SVG with no
`viewBox` renders as a solid colour tile under `object-fit: cover` — the <rect>
fills but the <text> is scaled out of view, so cards showed blank colour with no
title and the mocked suite stayed green. The viewBox assertion below is that
regression lock.
"""

from urllib.parse import unquote

from backend.poster import title_card_data_uri


def _decode(uri: str) -> str:
    assert uri.startswith("data:image/svg+xml;utf8,")
    return unquote(uri.split(",", 1)[1])


def test_data_uri_prefix_and_svg():
    svg = _decode(title_card_data_uri("星界航線", seed_key="mock-004"))
    assert svg.startswith("<svg") and svg.endswith("</svg>")


def test_viewbox_present():
    # Required so an <img> with object-fit:cover scales the whole canvas instead
    # of clipping the <text> out — the bug this test exists to catch.
    svg = _decode(title_card_data_uri("極速通緝"))
    assert 'viewBox="0 0 300 450"' in svg


def test_title_rendered_in_text():
    svg = _decode(title_card_data_uri("燈塔守候", seed_key="mock-007"))
    assert "<text" in svg
    assert "燈塔守候" in svg


def test_title_is_xml_escaped():
    svg = _decode(title_card_data_uri("Tom & Jerry <Reboot>"))
    assert "&amp;" in svg and "&lt;" in svg and "&gt;" in svg
    # The raw special chars must not leak through unescaped into markup.
    assert " & " not in svg and "<Reboot>" not in svg


def test_hue_deterministic_and_seed_scoped():
    # Same seed_key → identical poster (stable across runs); different seed_key
    # → (almost always) a different hue, so distinct films look distinct.
    a = title_card_data_uri("同名片", seed_key="film-a")
    a2 = title_card_data_uri("同名片", seed_key="film-a")
    b = title_card_data_uri("同名片", seed_key="film-b")
    assert a == a2
    assert a != b


def test_seed_key_falls_back_to_title():
    # No seed_key → hue derived from the title, still deterministic.
    assert title_card_data_uri("無種子") == title_card_data_uri("無種子")
