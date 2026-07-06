"""Page 5 — Award Tracker Dashboard. Shows TMDB-enriched nominees per ceremony."""

import json
from collections import defaultdict
from pathlib import Path

from nicegui import app, run, ui

from frontend.api_client import api
from frontend.components import film_list
from frontend.components.award_card import award_card
from frontend.components.layout import CARD_GRID_4
from frontend.components.messages import error_label
from frontend.components.page_header import page_header
from frontend.components.poster import award_poster_chain
from frontend.i18n import t

# Prettier chrome for the ceremony list, applied only when a non-default style
# is active (matches the global style switch). Turns the bare Quasar accordion
# into bordered cards with a trophy accent + year chip.
_AWARDS_CSS = """
<style>
  .awc-legend { display:inline-flex; align-items:center; gap:8px; font-size:.82rem; color:#9a9a9a;
    border:1px solid #262626; background:#141414; border-radius:10px; padding:7px 14px; margin:2px 0 20px; }
  .awc-cer { border:1px solid #242424 !important; border-radius:14px !important;
    background:#141414 !important; margin-bottom:12px !important; overflow:hidden;
    transition:border-color .16s ease, box-shadow .16s ease; }
  .awc-cer:hover { border-color:rgba(242,111,33,.45) !important; box-shadow:0 8px 26px rgba(0,0,0,.45); }
  .awc-cer > .q-expansion-item__container > .q-item { padding:14px 18px; }
  .awc-ico { color:#f2c037; }
  .awc-nm { font-weight:700; font-size:1.02rem; }
  .awc-yr { background:rgba(242,111,33,.14); color:#f2a93b; border:1px solid rgba(242,111,33,.4);
    border-radius:999px; padding:1px 11px; font-weight:700; font-size:.78rem; }
  /* Tier 1 — TMDb-style award cards: a big logo banner on top, meta below */
  .awo-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:14px; width:100%; }
  .awo-card { border-radius:12px; cursor:pointer; overflow:hidden; display:flex; flex-direction:column;
    background:#1f1f1f; border:1px solid #262626; text-decoration:none !important; color:inherit !important;
    transition:transform .15s ease, box-shadow .15s ease, border-color .15s ease; }
  .awo-card:hover { transform:translateY(-3px); border-color:rgba(242,111,33,.5);
    box-shadow:0 10px 28px rgba(0,0,0,.55); }
  /* Square banner: the logo PNG already carries its own bg (black/white), so
     fill edge-to-edge (cover) — no white padding ring. Trophy fallback centred. */
  .awo-top { aspect-ratio:1; display:flex; align-items:center; justify-content:center; overflow:hidden; }
  .awo-top.logo { padding:0; background:#000; }
  .awo-top.logo img { width:100%; height:100%; object-fit:cover; }
  /* Trophy emblem as a centered background — no child element, so
     background-position:center guarantees true centering (the q-img/html
     wrapper threw the % sizing off). */
  .awo-top.trophy {
    background:
      url("/assets/award-emblem.svg?v=3") center / 50% auto no-repeat,
      linear-gradient(135deg, #241910, #141414 65%);
  }
  .awo-meta { padding:12px 15px; }
  .awo-meta .nm { font-weight:800; font-size:1rem; line-height:1.25; color:#efefef; }
  .awo-meta .yr { color:#8a8a8a; font-size:.78rem; margin-top:2px; }
  .awo-meta .hv { display:inline-block; margin-top:9px; border:1px solid rgba(242,111,33,.5);
    color:#f26f21; border-radius:999px; padding:1px 10px; font-size:.73rem; font-weight:700; }
  /* Tier 2 — single-award page: full-bleed gradient hero (TMDb-style) */
  .awo-hero { width:100vw; margin-left:calc(50% - 50vw); padding:52px 0;
    background:linear-gradient(120deg,#2a1c0e,#141414 66%); }
  .awo-hero-in { width:100%; max-width:1152px; margin:0 auto; padding:0 24px; }
  .awo-hero .kick { letter-spacing:3px; font-size:.8rem; color:#f26f21; font-weight:700; }
  .awo-hero .h { font-size:2.4rem; font-weight:800; margin:5px 0 12px; }
  .awo-hero .bl { max-width:680px; color:#cfcfcf; font-size:.92rem; line-height:1.55; }
</style>
"""


# org_id → TMDb award logo URL (only the awards TMDb actually carries; others
# fall back to the trophy emoji). Scraped once into award_logos.json.
_AWARD_LOGOS = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "award_logos.json").read_text("utf-8")
)


def _nom_to_film(nom: dict) -> dict:
    """Adapt an award nomination to the shape film_list.render expects, so the
    matched-film spotlight reuses the exact grid/bubble styling as search."""
    won = (nom.get("result") or "").lower() == "won"
    chain = [u for u in award_poster_chain(nom) if u]
    return {
        "film_id": nom.get("matched_film_id") or "",
        "title_zh": (
            nom.get("matched_title_zh")
            or nom.get("tmdb_title")
            or nom.get("film_title_primary")
            or ""
        ),
        "poster_url": chain[0] if chain else "",
        "won": won,
        "badge": t("card.award_won") if won else t("card.award_nominee"),
        "reason": nom.get("category") or "",
    }


def awards_page():
    # style != default → curation layout (matched films as poster heroes);
    # default → the classic award_card grid. Driven by the global style switch.
    style = app.storage.user.get("style", "default")
    if style != "default":
        ui.add_head_html(_AWARDS_CSS)
    page_header(t("awards.title"), t("awards.desc"))
    # Legend for the "a/b" pattern shown on every ceremony / category row.
    # Orange = "我們有的片數" (matches the CTA brand color throughout).
    legend = ui.html(t("awards.legend_html"))
    legend.classes("awc-legend" if style != "default" else "q-mb-lg")

    try:
        orgs = {o["org_id"]: o for o in api.list_award_orgs()}
    except Exception as e:
        error_label(t("awards.backend_unreachable", e=e))
        return

    try:
        batches = api.recent_award_batches(limit=1000)
    except Exception as e:
        error_label(t("awards.load_batches_failed", e=e))
        batches = []

    if not batches:
        with ui.card().classes("q-pa-md w-full").style("border-left: 4px solid #f2a93b;"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("info").classes("text-warning")
                ui.label(t("awards.empty_title")).classes("text-subtitle1 font-bold")
            ui.label(t("awards.empty_hint")).classes("text-caption text-grey")
            ui.label(t("awards.registered_orgs")).classes("text-caption q-mt-sm")
            with ui.row().classes("gap-1 flex-wrap"):
                for o in orgs.values():
                    ui.badge(o["name_zh"], color="blue").props("outline")
        return

    ceremonies = _group_by_ceremony(batches, orgs)

    # Tier 1 (non-default style): award-card grid → click an award to open its
    # page (/awards/{org}). Default style keeps the flat ceremony accordion.
    if style != "default":
        orgs_agg = _group_by_org(ceremonies)
        orgs_agg.sort(key=lambda o: (o["matched"], o["year"]), reverse=True)
        ui.label(t("awards.recent")).classes("text-h6 q-mb-sm")
        with ui.element("div").classes("awo-grid"):
            for o in orgs_agg:
                card = ui.link(target=f"/awards/{o['org_id']}").classes("awo-card")
                with card:
                    logo = _AWARD_LOGOS.get(o["org_id"])
                    top = ui.element("div").classes("awo-top logo" if logo else "awo-top trophy")
                    with top:
                        if logo:
                            ui.image(logo)
                        # trophy: emblem is the centered CSS background (no child)
                    with ui.element("div").classes("awo-meta"):
                        ui.label(o["org_label"]).classes("nm")
                        ui.label(str(o["year"])).classes("yr")
                        ui.label(t("awards.lib_count", n=o["matched"])).classes("hv")
        return

    ceremonies.sort(key=lambda c: c["latest_insert"], reverse=True)
    ui.label(t("awards.recent")).classes("text-h6 q-mb-sm")
    for c in ceremonies:
        _render_ceremony_card(c, style)


def _group_by_org(ceremonies: list[dict]) -> list[dict]:
    """Collapse ceremonies into one entry per award org for the Tier-1 grid:
    keep the latest year and that year's in-library matched-film count."""
    by_org: dict[str, dict] = {}
    for c in ceremonies:
        o = by_org.setdefault(
            c["org_id"],
            {"org_id": c["org_id"], "org_label": c["org_label"], "year": 0, "matched": 0},
        )
        if c["year"] >= o["year"]:
            o["year"] = c["year"]
            o["matched"] = c["nominated_films_matched"]
    return list(by_org.values())


def award_org_page(org_id: str):
    """Tier 2 — a single award's page: hero + its ceremonies (expanded), each
    with the in-library matched rail + per-category list."""
    style = app.storage.user.get("style", "default")
    ui.add_head_html(_AWARDS_CSS)
    ui.link(t("awards.back"), "/awards").classes("text-caption")

    try:
        orgs = {o["org_id"]: o for o in api.list_award_orgs()}
        batches = api.recent_award_batches(limit=1000)
    except Exception as e:
        error_label(t("awards.backend_unreachable", e=e))
        return

    ceremonies = [c for c in _group_by_ceremony(batches, orgs) if c["org_id"] == org_id]
    if not ceremonies:
        with ui.column().classes("q-pa-lg w-full max-w-6xl mx-auto"):
            error_label(t("awards.org_not_found"))
        return

    ceremonies.sort(key=lambda c: c["year"], reverse=True)

    # Full-bleed gradient hero (TMDb-style).
    with ui.element("div").classes("awo-hero"), ui.element("div").classes("awo-hero-in"):
        ui.label("AWARDS").classes("kick")
        ui.label(ceremonies[0]["org_label"]).classes("h")
        ui.label(t("awards.org_blurb")).classes("bl")

    with ui.column().classes("q-pa-lg w-full max-w-6xl mx-auto"):
        ui.link(t("awards.back"), "/awards").classes("text-caption")
        for c in ceremonies:
            _render_ceremony_card(c, style, expand=True)


def _tag_to_org(tag_id: str, orgs: dict[str, dict]) -> dict | None:
    for o in orgs.values():
        if tag_id.startswith(o["tag_prefix"] + "-") or tag_id == o["tag_prefix"]:
            return o
    return None


def _group_by_ceremony(batches: list[dict], orgs: dict[str, dict]) -> list[dict]:
    """Collapse the (tag_id, year) batches into (org_id, year) ceremonies.

    Ceremony-level distinct-film counts come from the API on every batch row;
    they are identical for all rows of the same ceremony so we just copy the
    first one we see (no summing — that's the bug we are fixing).
    """
    grouped: dict[tuple[str, int], dict] = defaultdict(
        lambda: {
            "org_id": "",
            "org_label": "",
            "year": 0,
            "nominated_films_total": 0,
            "won_films_total": 0,
            "nominated_films_matched": 0,
            "won_films_matched": 0,
            "latest_insert": "",
            "categories": [],
        }
    )
    for b in batches:
        org = _tag_to_org(b["tag_id"], orgs)
        org_id = org["org_id"] if org else b["tag_id"].split("-")[0]
        org_label = org["name_zh"] if org else org_id
        key = (org_id, b["year"])
        g = grouped[key]
        g["org_id"] = org_id
        g["org_label"] = org_label
        g["year"] = b["year"]
        # Distinct-film metrics are ceremony-wide, identical across category
        # rows. Take the larger value in case one row arrived before others
        # (defensive — should always be equal in practice).
        g["nominated_films_total"] = max(
            g["nominated_films_total"], b.get("ceremony_nominated_films_total", 0)
        )
        g["won_films_total"] = max(g["won_films_total"], b.get("ceremony_won_films_total", 0))
        g["nominated_films_matched"] = max(
            g["nominated_films_matched"], b.get("ceremony_nominated_films_matched", 0)
        )
        g["won_films_matched"] = max(g["won_films_matched"], b.get("ceremony_won_films_matched", 0))
        g["latest_insert"] = max(g["latest_insert"], b["latest_insert"])
        g["categories"].append(b)
    return list(grouped.values())


def _render_ceremony_card(c: dict, style: str = "default", expand: bool = False):
    # Use a custom slot so the matched-film counts can be tinted brand orange
    # without printing the literal "(片庫)" suffix on every row.
    counts_html = t(
        "awards.counts_html",
        nominated_matched=c["nominated_films_matched"],
        nominated_total=c["nominated_films_total"],
        won_matched=c["won_films_matched"],
        won_total=c["won_films_total"],
    )
    exp = ui.expansion().classes("w-full awc-cer" if style != "default" else "w-full q-mb-md")
    with exp.add_slot("header"):
        if style != "default":
            # Card-style header: trophy accent + org name + year chip, counts
            # pushed to the right.
            with ui.row().classes("items-center gap-3 w-full no-wrap"):
                ui.icon("emoji_events").classes("awc-ico")
                ui.label(c["org_label"]).classes("awc-nm")
                ui.label(str(c["year"])).classes("awc-yr")
                ui.space()
                ui.html(counts_html)
        else:
            with ui.row().classes("items-center gap-2 w-full"):
                ui.label(t("awards.ceremony_title", org=c["org_label"], year=c["year"])).classes(
                    "text-subtitle1"
                )
                ui.html(counts_html)
    state = {"loaded": False}
    with exp:
        body = ui.column().classes("w-full q-pa-sm")
        with body, ui.row().classes("items-center justify-center gap-2 w-full q-pa-md"):
            ui.spinner(size="md", color="amber")
            ui.label(t("awards.loading")).classes("text-caption text-grey")

    async def load():
        if state["loaded"]:
            return
        state["loaded"] = True
        try:
            noms = await run.io_bound(
                api.list_award_nominations,
                org_id=c["org_id"],
                year=c["year"],
                limit=500,
            )
        except Exception as e:
            try:
                body.clear()
                with body:
                    error_label(t("awards.load_failed", e=e))
            except RuntimeError:
                pass  # slot gone
            return

        try:
            body.clear()
        except RuntimeError:
            return  # navigated away before the deferred load finished — slot gone
        with body:
            if not noms:
                ui.label(t("awards.no_records")).classes("text-caption text-grey")
                return

            matched_by_film: dict[str, dict] = {}
            for n in noms:
                fid = n.get("matched_film_id")
                if fid and fid not in matched_by_film:
                    matched_by_film[fid] = n

            # Matched (in-library) films — spotlight. Curation style = poster
            # heroes (gold winners); default = classic award_card grid.
            if matched_by_film:
                if style != "default":
                    # Reuse the shared film_list renderer (same grid/bubble look
                    # as search) — uniform size, winners first + gold border.
                    cards = sorted(
                        matched_by_film.values(),
                        key=lambda n: (n.get("result") or "").lower() != "won",
                    )
                    film_list.render([_nom_to_film(n) for n in cards], style, tiered=False)
                else:
                    with ui.grid().classes(f"{CARD_GRID_4} gap-3 q-mt-sm q-mb-md w-full"):
                        for n in matched_by_film.values():
                            award_card(n)
                ui.separator()

            def _by_cat() -> None:
                by_cat: dict[str, list[dict]] = defaultdict(list)
                for n in noms:
                    by_cat[n.get("category") or "—"].append(n)
                for cat, items in by_cat.items():
                    noms_total = {
                        x.get("film_title_primary") for x in items if x.get("film_title_primary")
                    }
                    noms_won = {
                        x.get("film_title_primary")
                        for x in items
                        if x.get("result") == "won" and x.get("film_title_primary")
                    }
                    lib_noms = {x.get("matched_film_id") for x in items if x.get("matched_film_id")}
                    lib_won = {
                        x.get("matched_film_id")
                        for x in items
                        if x.get("result") == "won" and x.get("matched_film_id")
                    }
                    cat_exp = ui.expansion(icon="category").classes("w-full")
                    with cat_exp.add_slot("header"):
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.label(cat).classes("text-body1")
                            ui.html(
                                t(
                                    "awards.counts_html",
                                    nominated_matched=len(lib_noms),
                                    nominated_total=len(noms_total),
                                    won_matched=len(lib_won),
                                    won_total=len(noms_won),
                                )
                            )
                    with cat_exp:
                        if style != "default":
                            # Bubble: reuse the shared film_list (matched films
                            # link to detail; unmatched show the poster only).
                            film_list.render([_nom_to_film(n) for n in items], style, tiered=False)
                        else:
                            with ui.grid().classes(f"{CARD_GRID_4} gap-3 q-mt-sm w-full"):
                                for n in items:
                                    award_card(n)

            # Curation style: the full per-category list is secondary → collapse
            # it. Default: show it open as before.
            if style != "default":
                with ui.expansion(t("awards.by_category")).classes("w-full q-mt-md"):
                    _by_cat()
            else:
                ui.label(t("awards.by_category")).classes(
                    "text-subtitle2 q-mt-sm q-mb-xs text-grey-8"
                )
                _by_cat()

    async def _on_change(e) -> None:
        if e.value:
            await load()

    exp.on_value_change(_on_change)

    # Tier-2 award page opens its ceremonies up-front (no click needed).
    if expand:
        exp.value = True
        ui.timer(0.1, load, once=True)
