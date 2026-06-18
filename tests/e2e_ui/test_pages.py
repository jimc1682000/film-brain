"""Browser render-smoke for the key pages + the bubble-poster geometry lock.

LOCAL-ONLY / opt-in — run via `make e2e-ui` (booted by scripts/e2e_ui.sh).
Lives OUTSIDE backend/tests/ so the default pytest run / per-PR CI never
collects it (pyproject testpaths = ["backend/tests"]).

What it asserts (the "baseline" is code + a fixed seed, NOT golden images):
  * every page renders with no browser console error / uncaught page error;
  * a signature heading is visible (chrome rendered);
  * content pages show a known seeded title (data rendered, not just chrome);
  * /awards/{org} in BUBBLE style: every poster fills its 16:9 frame so the
    title-card's centred title is not clipped out — the regression lock for the
    "solid colour tile, no title" bug.

NOT covered: the /search round-trip (query-time embedding needs Ollama; search
*quality* is scripts/eval_search.py's job). The home page is render-only here.
"""

from __future__ import annotations

import pytest
from helpers import open_page, snap
from playwright.sync_api import expect

# path, artifact-name, signature text that must be VISIBLE (substring match)
PAGES = [
    ("/", "home", "想看什麼"),
    ("/browse", "browse", "標籤瀏覽"),
    ("/auto-tag", "autotag", "自動標籤"),
    ("/feedback", "feedback", "洞察與回饋"),
    ("/awards", "awards", "獎項追蹤"),
    ("/awards/oscars", "awards_oscars", "奧斯卡金像獎"),
    ("/film/mock-007", "film_detail", "燈塔守候"),
]


@pytest.mark.parametrize(("path", "name", "signature"), PAGES, ids=[p[1] for p in PAGES])
def test_page_renders(page, ui_base, artifacts, errors, path, name, signature):
    open_page(page, ui_base, path)
    snap(page, artifacts, name)
    expect(page.get_by_text(signature).first).to_be_visible()
    assert not errors, f"{path} console errors: {errors}"


def test_awards_oscars_shows_seeded_film(page, ui_base, artifacts, errors):
    # Content (not just chrome): a seeded oscars nominee that matched a mock film.
    open_page(page, ui_base, "/awards/oscars")
    expect(page.get_by_text("機械叛變").first).to_be_visible()
    assert not errors


def test_bubble_poster_title_not_clipped(page, ui_base, artifacts, errors):
    """THE regression lock. The bubble layout (.fl-p, aspect-ratio 16/9) renders
    the poster as a q-img that sizes to the image's natural portrait ratio; if it
    overflows the frame, overflow:hidden clips the lower half and the title-card's
    centred title vanishes (blank colour tile). Assert every poster's rendered
    height fits its frame and is object-fit:cover, so the centre stays visible."""
    open_page(page, ui_base, "/awards/oscars")
    # flip the global style switch: 預設卡片 -> 泡泡 (curation/poster-hero layout)
    page.click(".q-select")
    page.wait_for_timeout(500)
    page.click(".q-menu .q-item:has-text('泡泡')")
    page.wait_for_timeout(2500)
    snap(page, artifacts, "awards_oscars_bubble")

    metrics = page.eval_on_selector_all(
        ".fl-p",
        """els => els.map(el => {
            const img = el.querySelector('img');
            const f = el.getBoundingClientRect();
            const i = img.getBoundingClientRect();
            return {fh: f.height, ih: i.height, fit: getComputedStyle(img).objectFit};
        })""",
    )
    assert metrics, "no .fl-p poster cells found — bubble layout did not render"
    for m in metrics:
        # ih=509 vs fh=193 was the bug; ih<=fh means the frame crops, not clips.
        assert m["ih"] <= m["fh"] + 1, f"poster overflows frame (title clipped): {m}"
        assert m["fit"] == "cover", f"poster not object-fit:cover: {m}"
    assert not errors
