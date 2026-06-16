"""NiceGUI frontend — AI Film Library Brain with 3 pages.

Theme: aligned with catchplay.com/tw (dark mode + signature orange #f26f21).
Palette extracted from the live CSS variables on 2026-05-26:
    --global-color-cp:        #f26f21  (primary CTA / brand)
    --global-color-cp-hover:  #ff944c
    --global-color-cp-tap:    #d4570c
    --global-color-black:     #000     (page background)
    --global-color-gray-500:  #1f1f1f  (card background)
    --global-color-gray-000:  #efefef  (body text)
    --global-color-red:       #d0021b
    --global-color-yellow:    #f2a93b
    --global-color-blue:      #00a3d9
    --global-color-green:     #1ac130
"""

import os
from pathlib import Path

from nicegui import app, ui

from frontend.components.film_list import _TIER_CSS, STYLES
from frontend.i18n import t
from frontend.pages.auto_tag import auto_tag_page
from frontend.pages.awards import award_org_page, awards_page
from frontend.pages.browse import browse_page
from frontend.pages.detail import detail_page
from frontend.pages.feedback import feedback_page
from frontend.pages.search import search_page

# Serve brand assets (CP+ logo used as the poster placeholder).
app.add_static_files("/assets", str(Path(__file__).parent / "assets"))

_BRAND_CSS = """
<style>
  body, .nicegui-content, .q-page {
    background: #000 !important;
    color: #efefef !important;
    font-family: roboto, "PingFang TC", "Microsoft JhengHei", sans-serif;
  }
  /* Cards: subtle border + lift on hover (matches the docs site) */
  .q-card {
    background: #1f1f1f !important;
    color: #efefef !important;
    border: 1px solid #262626;
    border-radius: 12px;
    transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
  }
  .q-card:hover {
    transform: translateY(-3px);
    border-color: rgba(242,111,33,.5);
    box-shadow: 0 10px 28px rgba(0,0,0,.55);
  }
  .q-header { background: #000 !important; border-bottom: 1px solid #1f1f1f; }
  a, .text-primary { color: #f26f21 !important; }
  a:hover { color: #ff944c !important; }

  /* Nav links: clean, no underline, hover to brand */
  .q-header a {
    text-decoration: none !important;
    color: #d4d4d4 !important;
    font-weight: 500;
    opacity: .9;
    transition: color .15s ease, opacity .15s ease;
  }
  .q-header a:hover { color: #f26f21 !important; opacity: 1; }
  /* Active nav item painted brand orange (matches the film-brain docs site) */
  .q-header a.nav-active { color: #f26f21 !important; opacity: 1; font-weight: 700; }

  /* Dark inputs — kill the bright white search box on black */
  .q-field--outlined .q-field__control { background: #161616 !important; border-radius: 10px; }
  .q-field--outlined .q-field__control:before { border-color: #3a3a3a !important; }
  .q-field--outlined .q-field__control:hover:before { border-color: #555 !important; }
  .q-field--outlined.q-field--focused .q-field__control:after { border-color: #f26f21 !important; }
  .q-field__native, .q-field__input, .q-field__label { color: #efefef !important; }

  .q-btn { border-radius: 10px; }

  /* Brand gradient text (hero heading) */
  .cp-gradient {
    background: linear-gradient(120deg, #ff944c, #f26f21 55%, #d4570c);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  /* Demo queries rendered as pills */
  .cp-chip {
    border: 1px solid #3a3a3a !important;
    border-radius: 999px !important;
    background: #161616 !important;
    color: #cfcfcf !important;
    transition: border-color .15s ease, color .15s ease;
  }
  .cp-chip:hover { border-color: #f26f21 !important; color: #fff !important; }
</style>
"""


def header(active: str = ""):
    """Shared navigation header — CATCHPLAY+ dark + orange accent.

    `active` is the href of the current page so its nav link is painted brand
    orange (matches the film-brain docs site). Brand palette + global CSS are
    applied per-page (inside header()) because NiceGUI rejects module-level UI
    calls when `@ui.page` is also in use.
    """
    ui.colors(
        primary="#f26f21",
        secondary="#1f1f1f",
        accent="#ff944c",
        dark="#000000",
        positive="#1ac130",
        negative="#d0021b",
        warning="#f2a93b",
        info="#00a3d9",
    )
    ui.add_head_html(_BRAND_CSS)
    # Inject film_list styles at page setup — render() also adds them, but when
    # film_list renders inside a deferred/async load (awards ceremony, similar
    # films) the late add_head_html no longer reaches the already-sent <head>.
    ui.add_head_html(_TIER_CSS)
    with (
        ui.header().classes("text-white").style("background: #000;"),
        ui.row().classes("items-center gap-4 w-full"),
    ):
        # Logo links back to the Search hero (standard "logo = home" pattern,
        # also matches catchplay.com/tw). No underline so it reads as branding.
        ui.link(t("nav.brand"), "/").classes("text-h6").style(
            "color: #f26f21; text-decoration: none;"
        )
        ui.space()
        # Browse (/browse) + Feedback (/feedback) routes stay live but are
        # hidden from nav — not part of the current demo story.
        for label, href in (
            (t("nav.search"), "/"),
            (t("nav.autotag"), "/auto-tag"),
            (t("nav.awards"), "/awards"),
        ):
            # Active link drops text-white so .nav-active's orange isn't
            # overridden by Quasar's !important white.
            ui.link(label, href).classes("nav-active" if href == active else "text-white")
        # 技術說明 → the public VitePress portfolio site (deeper, maintained
        # docs). Opens in a new tab.
        ui.link(t("nav.brief"), "https://jimc1682000.github.io/film-brain/", new_tab=True).classes(
            "text-white"
        )
        # Relative path so it resolves on both localhost and the demo
        # subdomain; Swagger is mounted at /api/docs (see backend/main.py).
        ui.link(t("nav.api_docs"), "/api/docs", new_tab=True).classes("text-white text-caption")

        # Global result-style switch (right of API docs). Persists in
        # app.storage.user so every film-listing page reskins together.
        def _set_style(e):
            app.storage.user["style"] = e.value
            ui.navigate.reload()

        ui.select(
            options={k: t(v) for k, v in STYLES.items()},
            value=app.storage.user.get("style", "default"),
            label=t("style.label"),
            on_change=_set_style,
        ).props("dense outlined options-dense").classes("w-32 text-caption")


@ui.page("/")
def index(q: str = ""):
    header("/")
    with ui.column().classes("q-pa-lg w-full max-w-6xl mx-auto"):
        search_page(initial_query=q)


@ui.page("/auto-tag")
def auto_tag():
    header("/auto-tag")
    with ui.column().classes("q-pa-lg w-full max-w-6xl mx-auto"):
        auto_tag_page()


@ui.page("/browse")
def browse():
    header()
    with ui.column().classes("q-pa-lg w-full max-w-6xl mx-auto"):
        browse_page()


@ui.page("/film/{film_id}")
def film_detail(film_id: str):
    # detail_page self-centers its content; the hero stays full-bleed outside
    # any max-width column so the backdrop spans the whole browser width.
    header()
    detail_page(film_id)


@ui.page("/awards")
def awards():
    header("/awards")
    with ui.column().classes("q-pa-lg w-full max-w-6xl mx-auto"):
        awards_page()


@ui.page("/awards/{org_id}")
def award_org(org_id: str):
    # No centered wrapper — award_org_page renders a full-bleed hero then
    # centers its own content (TMDb-style award page).
    header("/awards")
    award_org_page(org_id)


@ui.page("/feedback")
def feedback():
    header()
    with ui.column().classes("q-pa-lg w-full max-w-6xl mx-auto"):
        feedback_page()


ui.run(
    host="0.0.0.0",
    port=int(os.getenv("FRONTEND_PORT", "8080")),
    title="AI Film Library Brain — CATCHPLAY+",
    dark=True,  # match catchplay.com/tw which ships dark mode by default
    language="zh-TW",
    favicon=str(Path(__file__).parent / "assets" / "favicon.svg"),  # brand clapperboard mark
    # Signs the app.storage.user cookie (only holds a UI style preference, no
    # auth/PII) — env-overridable so a deploy isn't stuck on the public default.
    storage_secret=os.getenv("STORAGE_SECRET", "film-brain-demo"),
)
