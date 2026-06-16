"""Page 1 — Semantic search (Hero Demo Feature)."""

import json
from pathlib import Path
from urllib.parse import quote

from nicegui import app, run, ui

from frontend.api_client import api
from frontend.components import film_list
from frontend.components.loading_dialog import blocking_loader
from frontend.components.page_header import page_header
from frontend.i18n import t

# Demo chips live in one file (frontend/chips.json) so the backend can warm
# the exact same queries at startup — single source, no drift.
_CHIPS_PATH = Path(__file__).resolve().parent.parent / "chips.json"


def _demo_chips() -> list[str]:
    try:
        return json.loads(_CHIPS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def search_page(initial_query: str = ""):
    """Build the semantic search page.

    Layout: heading + search bar stay pinned near top (Google-style);
    results render below. Active query + full result list are mirrored
    into URL (`?q=...`) + `history.state` so browser Back from detail
    restores results instantly with no backend call.
    """

    # --- Top: heading + search bar (always visible) ---
    page_header(t("search.title"), t("search.desc"))

    with ui.row().classes("w-full items-end gap-4"):
        search_input = (
            ui.input(
                label=t("search.input_label"),
                value=initial_query,
                placeholder=t("search.input_placeholder"),
            )
            .classes("flex-grow")
            .props("outlined")
        )

        top_k_select = ui.select(
            options={"5": "5", "10": "10", "20": "20"}, value="10", label=t("search.results_count")
        ).classes("w-24")

        ui.button(t("search.btn"), on_click=lambda: enter_gate(), icon="search").props(
            "color=primary"
        )

    # Demo queries — single source in frontend/chips.json (backend warms these).
    ui.label(t("search.try_these")).classes("text-caption text-grey q-mt-sm")
    with ui.row().classes("gap-2"):
        for demo_q in _demo_chips():
            ui.button(demo_q, on_click=lambda *_, q=demo_q: run_demo(q)).props(
                "flat dense no-caps"
            ).classes("cp-chip text-xs")

    # --- Below: status + results ---
    status_label = ui.label("").classes("text-grey q-mt-md")
    results_container = ui.column().classes("w-full")

    def _render_understanding(u: dict, editable: bool = False):
        # How the system read the query — filters + keywords + award flag,
        # plus the HyDE plot (the WHY behind pure-semantic hits) and the
        # low-confidence notice for out-of-domain queries.
        # editable=True (gate): the tags/keywords render AS removable ✕ chips
        # (the understood signals ARE the actionable layer — no duplicate badge
        # row). editable=False (results): static badges.
        filters = u.get("filters") or []
        kws = u.get("keywords") or []
        hyde = (u.get("hyde_text") or "").strip()
        conf = u.get("confidence")  # high | mid | low — drives the banner
        degraded = bool(u.get("degraded"))  # LLM expansion failed → honest fallback
        if (
            not filters
            and not kws
            and not u.get("award_required")
            and not hyde
            and not conf
            and not degraded
        ):
            return
        # Confidence banner: the honest query-level signal (from the primary-
        # cosine tier). high = real match, mid = partial, low = no real match.
        banners = {
            "high": ("✅ ", "search.conf_high", "#1ac130", "rgba(26,193,48,0.10)", "text-positive"),
            "mid": ("◐ ", "search.conf_mid", "#f2a93b", "rgba(242,169,59,0.10)", "text-warning"),
            "low": ("⚠ ", "search.low_conf", "#f26f21", "rgba(242,111,33,0.10)", "text-primary"),
        }
        if conf in banners:
            icon, key, border, bg, txt = banners[conf]
            with (
                ui.row()
                .classes("items-center gap-2 q-pa-sm rounded w-full flex-wrap q-mt-sm")
                .style(f"border: 1px solid {border}; background: {bg};")
            ):
                ui.label(icon + t(key)).classes(f"text-caption {txt}")
        with (
            ui.column()
            .classes("gap-1 q-pa-sm rounded w-full q-mt-sm")
            .style("border: 1px solid #00a3d9; background: rgba(0,163,217,0.08);")
        ):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.label(t("search.understood")).classes("text-caption font-bold text-info")
                if not degraded and u.get("award_required"):
                    ui.badge(t("search.award_req"), color="amber").props("outline").classes(
                        "text-xs"
                    )
                if not degraded and not editable:
                    # Results page: static badges + keyword line.
                    for f in filters:
                        ui.badge(f, color="blue").props("outline").classes("text-xs")
                    if kws:
                        ui.label(t("search.kw") + ": " + "、".join(kws)).classes(
                            "text-caption text-grey"
                        )
            if not degraded and editable:
                # Gate: the understood tags + keywords ARE removable ✕ chips.
                # Dedupe (keywords routinely echo filter labels 喜劇/紓壓/…).
                merged: list[str] = []
                _seen: set[str] = set()
                for lb in list(filters) + list(kws):
                    if lb and lb not in _seen:
                        _seen.add(lb)
                        merged.append(lb)
                if merged:
                    ui.label(t("search.gate_chips_hint")).classes("text-caption text-grey")
                    with ui.row().classes("gap-2 flex-wrap q-mt-xs"):
                        for lb in merged:
                            _make_remove_chip(lb)
            if degraded:
                # LLM parse unavailable → say so honestly + explain the fallback
                # (no silent empty box).
                ui.label(t("search.degraded")).classes("text-caption text-warning")
            elif hyde:
                ui.label(t("search.hyde") + ":「" + hyde + "」").classes("text-caption text-grey-5")

    def render_results(
        results: list[dict], query: str, from_cache: bool = False, understanding: dict | None = None
    ):
        badge = t("search.cached") if from_cache else ""
        status_label.text = t("search.found", n=len(results), query=query, cached=badge)
        results_container.clear()
        with results_container:
            if understanding:
                _render_understanding(understanding)
            if not results:
                ui.label(t("search.no_results")).classes("text-grey q-mt-md")
            else:
                film_list.render(results, app.storage.user.get("style", "default"))

    # Gate state. `refine` = the positive free-text box (folded into the query).
    # `excludes` = structured set of removed directions (chip ✕). Exclusions are
    # NOT folded into the query text — they go to the backend as a structured
    # list so the embedded/BM25 query stays positive (a folded "不要X" pollutes
    # both: dense recall ignores negation, BM25 still matches X). Positive →
    # query; negative → exclude. Two channels.
    gate: dict = {"refine": None, "excludes": set()}

    async def enter_gate():
        query = search_input.value.strip()
        if not query:
            ui.notify(t("search.empty_query"), type="warning")
            return
        gate["excludes"] = set()  # fresh search → reset accumulated exclusions
        await understand(query)

    async def understand(query: str):
        """Gate phase: ask the backend to interpret the query WITHOUT searching
        films, then show the interpretation for the user to steer."""
        status_label.text = t("search.gate_thinking")
        results_container.clear()
        # Anchor every UI op to the (always-alive) results_container slot. A gate
        # button triggers this; clearing the gate deletes that button mid-handler,
        # so without re-anchoring the loader dialog / notify would attach to the
        # deleted button's slot → "parent slot deleted" crash.
        with results_container:
            try:
                async with blocking_loader(
                    t("search.gate_thinking"),
                    t("search.gate_thinking_sub"),
                ):
                    data = await run.io_bound(
                        api.search,
                        query,
                        int(top_k_select.value or 10),
                        None,
                        0.3,
                        True,  # understand_only
                        list(gate["excludes"]),
                    )
            except Exception as e:
                status_label.text = t("search.error", e=e)
                ui.notify(str(e), type="negative")
                return
            u = data.get("understanding") or {}
            # LLM expansion failed → nothing meaningful to confirm. Don't make the
            # user approve garbage; fall straight through to keyword + vector search.
            if u.get("degraded"):
                ui.notify(t("search.gate_degraded"), type="warning")
                await run_full_search(query)
                return
        render_gate(query, u)

    def _make_remove_chip(label: str) -> None:
        # A ui.button styled as a chip — NOT ui.chip. Quasar's q-chip fires its
        # click only on a synthetic dispatch; a real mouse click (judge on stage)
        # doesn't reach the handler. A ui.button with on_click in the CONSTRUCTOR
        # (same pattern as the demo chips) takes real clicks reliably. Click =
        # record the exclusion (structured) + delete self. Applied on the next
        # 重新聯想 / 方向對 (sent as the backend `exclude` list).
        def _on_remove(_=None) -> None:
            gate["excludes"].add(label)
            btn.delete()

        btn = (
            ui.button(f"✕ {label}", on_click=_on_remove)
            .props("flat dense no-caps")
            .classes("cp-chip text-xs")
        )

    def render_gate(query: str, u: dict):
        status_label.text = t("search.gate_status")
        results_container.clear()
        with results_container:
            # editable=True → the understood tags/keywords ARE the removable
            # chips (no separate, duplicated badge row).
            _render_understanding(u, editable=True)
            gate["refine"] = (
                ui.textarea(
                    label=t("search.gate_refine_label"),
                    placeholder=t("search.gate_refine_ph"),
                )
                .classes("w-full q-mt-sm")
                .props("outlined autogrow")
            )
            with ui.row().classes("gap-2 q-mt-sm items-center"):
                ui.button(
                    t("search.gate_confirm"), icon="check", on_click=lambda: run_full_search(query)
                ).props("color=primary")
                ui.button(
                    t("search.gate_reloop"), icon="autorenew", on_click=lambda: reloop(query)
                ).props("outline color=primary")
                # Escape hatch — always reachable so a non-converging loop can't
                # trap the user (or the demo).
                ui.button(
                    t("search.gate_skip"), icon="bolt", on_click=lambda: run_full_search(query)
                ).props("flat color=grey")

    async def reloop(query: str):
        """Re-interpret with the user's POSITIVE correction folded into the query
        and the accumulated exclusions sent structurally. Either is enough to
        re-loop; corrections accumulate (query carries prior positive rounds,
        gate['excludes'] carries prior removals)."""
        box = gate.get("refine")
        refinement = box.value.strip() if box and box.value else ""
        if not refinement and not gate["excludes"]:
            ui.notify(t("search.gate_refine_empty"), type="info")
            return
        await understand(f"{query}。{refinement}" if refinement else query)

    async def run_full_search(query: str):
        """Commit: run the real hybrid search on the (possibly refined) query."""
        status_label.text = t("search.searching_status")
        results_container.clear()
        # Anchor to the stable results_container slot — a gate button may trigger
        # this and get deleted by the clear above (see understand() note).
        with results_container:
            try:
                async with blocking_loader(
                    t("search.searching"),
                    t("search.searching_sub"),
                ):
                    data = await run.io_bound(
                        api.search,
                        query,
                        int(top_k_select.value or 10),
                        None,
                        0.3,
                        False,  # understand_only
                        list(gate["excludes"]),
                    )
                results = data.get("results", [])
                render_results(
                    results, query, from_cache=False, understanding=data.get("understanding")
                )

                # Mirror query + full results + understanding into current history
                # entry. Browser Back from /film/{id} returns here; entry still
                # carries this state → restore path below skips the API call.
                state_payload = json.dumps(
                    {"q": query, "results": results, "understanding": data.get("understanding")}
                )
                ui.run_javascript(
                    f'history.replaceState({state_payload}, "", "/?q={quote(query)}")'
                )

                search_input.run_method("blur")

            except Exception as e:
                status_label.text = t("search.error", e=e)
                ui.notify(str(e), type="negative")

    async def run_demo(q: str):
        # Demo chips are curated queries → skip the gate, one click to results.
        search_input.value = q
        await run_full_search(q)

    async def restore_or_fetch():
        """On page load with ?q=..., prefer history.state over re-fetching.

        Browser Back carries the previous entry's state; if it matches the
        URL's query we render from cache instantly. Fresh reload or mismatch
        falls through to a normal API search.
        """
        try:
            state = await ui.run_javascript("return history.state", timeout=1.5)
        except Exception:
            state = None
        if (
            isinstance(state, dict)
            and state.get("q") == initial_query
            and isinstance(state.get("results"), list)
            and state["results"]
        ):
            render_results(
                state["results"],
                initial_query,
                from_cache=True,
                understanding=state.get("understanding"),
            )
            return
        # Fresh ?q load (shared link / reload): go straight to results, no gate.
        await run_full_search(initial_query)

    search_input.on("keydown.enter", enter_gate)

    if initial_query:
        ui.timer(0.1, restore_or_fetch, once=True)
