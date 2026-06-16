"""Shared film-list renderer — one entry point, swappable visual style.

Every place that lists films (search results, awards matches, similar films,
browse) calls render(films, style=...) so a single global style switch reskins
them all. Styles:
  - default : the existing film_card grid (full info card)
  - grid    : poster + % + one reason line; title on hover (desktop)
  - bubble  : poster + % only; title + reason on hover (desktop)

grid/bubble use rank tiers (2 sizes on phone, 3 on desktop) and keep the card
face clean — title/tags move to hover / the detail page. Pass tiered=False for
uniform cards (awards, where there is no relevance ranking — only a gold border
marks winners). A film dict may carry award extras: "badge" (text chip, shown
instead of the % score), "won" (gold border), "reason" (overrides the computed
match-reason line).
"""

from nicegui import ui

from frontend.components.film_card import film_card
from frontend.components.theme import score_color
from frontend.i18n import t

# value -> i18n label key (shown in the header switch). grid was folded into
# bubble (one clean layout), so only default + bubble remain selectable.
STYLES = {
    "default": "style.default",
    "bubble": "style.bubble",
}

_SRC_KEYS = {"vector": "card.src_vector", "hyde": "card.src_hyde", "bm25": "card.src_bm25"}

_TIER_CSS = """
<style>
  .fl-wall { display:flex; flex-wrap:wrap; gap:12px; width:100%; }
  .fl-cell { flex-grow:0; flex-shrink:0; min-width:0; cursor:pointer; }
  .fl-cell.t1 { flex-basis:calc(33.333% - 8px); }
  .fl-cell.t2 { flex-basis:calc(25% - 9px); }
  .fl-cell.t3 { flex-basis:calc(16.666% - 10px); }
  .fl-cell.u  { flex-basis:calc(33.333% - 8px); }
  @media (max-width:560px) {
    .fl-wall { gap:8px; }
    .fl-cell.t1 { flex-basis:100%; }
    .fl-cell.t2, .fl-cell.t3, .fl-cell.u { flex-basis:calc(50% - 4px); }
  }
  .fl-p { position:relative; aspect-ratio:16/9; border-radius:12px; overflow:hidden; border:1px solid #262626; }
  .fl-p img { width:100%; height:100%; object-fit:cover; display:block; }
  .fl-p.won { border:2px solid #f2c037; box-shadow:0 0 18px rgba(242,192,55,.22); }
  /* One unified status badge for both score % and award won/nominee. Dark
     translucent base → the poster behind never tints it (no colour bleed);
     the value rides on muted, opaque text → premium, quiet, still legible. */
  .fl-badge { position:absolute; top:6px; right:6px; z-index:3; padding:2px 7px;
    border-radius:7px; background:rgba(16,16,16,.74); backdrop-filter:blur(3px);
    font-size:.62rem; font-weight:800; letter-spacing:.2px; line-height:1.35;
    text-shadow:0 1px 2px rgba(0,0,0,.5); }
  .fl-badge.won { color:#f3c969; }
  .fl-badge.nom { color:#e7a36f; }
  .fl-ov { position:absolute; inset:0; display:flex; flex-direction:column; justify-content:flex-end;
    padding:10px 12px; background:linear-gradient(transparent 35%, rgba(0,0,0,.9)); opacity:0;
    transition:opacity .18s ease; }
  .fl-ov .t { font-weight:700; font-size:.9rem; line-height:1.2; }
  .fl-ov .w { color:#ccc; font-size:.72rem; margin-top:4px; }
  /* Each cell is a native <a> link. Reveal the overlay purely with :hover —
     desktop shows it on hover and a click navigates immediately; touch fires
     :hover on the first tap (showing info) and follows the link on the second.
     No JS state, so :hover always clears itself (no stuck-open cards). */
  .fl-cell { text-decoration:none !important; color:inherit !important; }
  .fl-p { transition:border-color .18s ease, box-shadow .18s ease; }
  .fl-cell:hover .fl-ov { opacity:1; }
  .fl-cell:hover .fl-p { border-color:rgba(242,111,33,.6); box-shadow:0 12px 30px rgba(0,0,0,.55); }
</style>
"""


def _tier(i: int) -> str:
    return "t1" if i < 3 else "t2" if i < 7 else "t3"


# score_color() returns a Quasar colour NAME, not a CSS value. Map it to bright
# brand-family hex that stays legible as the opaque % text on the dark fold.
_SC_HEX = {"positive": "#6ed496", "warning": "#e8c45e", "negative": "#e0655c"}


def _score_bg(value: float) -> str:
    return _SC_HEX.get(score_color(value), "#cccccc")


def _reason(film: dict) -> str:
    if film.get("reason") is not None:  # caller-supplied (e.g. awards category)
        return film["reason"]
    ex = film.get("explain") or {}
    srcs = [t(_SRC_KEYS[s]) for s in ex.get("sources", []) if s in _SRC_KEYS]
    prefs = (ex.get("matched_prefs") or [])[:4]
    if not prefs and not srcs:
        return ""
    if prefs:
        prefix = t("card.match") if ex.get("sources") else t("card.shared")
        txt = prefix + " " + "".join(f"[{p}]" for p in prefs)
        if srcs:
            txt += " · " + "+".join(srcs)
        return txt
    return t("card.hit", srcs="+".join(srcs))


def render(films: list[dict], style: str = "default", *, tiered: bool = True) -> None:
    if style not in ("grid", "bubble"):
        with ui.grid().classes("grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 q-mt-md w-full"):
            for f in films:
                film_card(f, show_score=True)
        return

    ui.add_head_html(_TIER_CSS)
    with ui.element("div").classes("fl-wall"):
        for i, f in enumerate(films):
            fid = f.get("film_id", "")
            cls = f"fl-cell {_tier(i) if tiered else 'u'}"
            # Native <a> link: one click navigates on desktop; on touch the
            # first tap fires :hover (reveals info), the second follows the link.
            cell = (
                ui.link(target=f"/film/{fid}").classes(cls)
                if fid
                else ui.element("div").classes(cls)
            )
            with cell:
                pic = "fl-p won" if f.get("won") else "fl-p"
                with ui.element("div").classes(pic):
                    ui.image(f.get("poster_url") or "").props("fit=cover")
                    # Top-right chip: % (translucent score colour) for
                    # search/similar; award won/nominee chip for awards.
                    if "score" in f:
                        ui.label(f"{int(f['score'] * 100)}%").classes("fl-badge").style(
                            f"color:{_score_bg(f['score'])}"
                        )
                    elif f.get("badge"):
                        ui.label(f["badge"]).classes(
                            "fl-badge won" if f.get("won") else "fl-badge nom"
                        )
                    with ui.element("div").classes("fl-ov"):
                        ui.label(f.get("title_zh", "")).classes("t")
                        ui.label(_reason(f)).classes("w")
