"""Reusable award ingest pipeline — official site as source, Wikipedia as verify.

Workflow:
  1. Primary source: each org's `official_url` from the registry
     (substitute {year} if present).
  2. Open in agent-browser, capture body innerText, ask Gemini → JSON.
  3. Verify: also pull the matching Wikipedia ceremony page, extract the same
     way, and diff against the primary set. Discrepancies logged as warnings.
  4. Call backend.award_manager.record_nomination for each primary nominee.

Usage:
  python -m scripts.award_pipeline --org oscars --year 2026
  python -m scripts.award_pipeline --org bafta --year 2026 \
    --url https://www.bafta.org/film/awards/winners-2026
  python -m scripts.award_pipeline --org oscars --year 2026 --no-verify
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.award_manager import get_org, record_nomination
from backend.config import settings
from backend.db import get_db

MODEL = settings.gemini_primary_model or "gemini-3.5-flash"
EXTRACT_CHAR_CAP = 180000

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_HEADERS = {
    "User-Agent": "AiFilmLibrary/1.0 (contact: https://github.com/jimc1682000/ai-film-library)"
}


def _wiki_api_from_host(host: str) -> str:
    return f"https://{host}/w/api.php"


ORDINAL_RE = re.compile(r"^\d+(st|nd|rd|th)\b", re.IGNORECASE)

NOMINEE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "film_title_primary": {"type": "string"},
            "film_title_alt": {"type": "string"},
            "person": {"type": "string"},
            "result": {"type": "string", "enum": ["won", "nominated"]},
        },
        "required": ["category", "film_title_primary", "result"],
    },
}


# ---------------- agent-browser ----------------


def ab(*args: str, timeout: int = 90) -> str:
    r = subprocess.run(
        ["agent-browser", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return (r.stdout or "") + (r.stderr or "")


def resolve_url(org: dict, year: int, override: str | None) -> str:
    if override:
        return override
    url = org["official_url"]
    if "{year}" in url:
        url = url.replace("{year}", str(year))
    return url


def fetch_page_text(url: str) -> str:
    ab("open", url, timeout=90)
    ab("wait", "--load", "networkidle", timeout=60)
    raw = ab("eval", "document.body.innerText", timeout=60)
    return raw.strip()


def _hrefs_by_keyword(keywords: list[str]) -> list[tuple[str, str]]:
    """Collect (url, label) pairs for anchors whose text matches any keyword."""
    pattern = "|".join(keywords)
    js = (
        f"Array.from(document.querySelectorAll('a')).filter(a=>"
        f"a.textContent.match(/{pattern}/i))"
        f".slice(0,30).map(a=>a.href+'||'+a.textContent.trim().replace(/\\s+/g,' '))"
        f".join('\\n')"
    )
    raw = ab("eval", js, timeout=30).strip()
    raw = raw.strip('"')
    out: list[tuple[str, str]] = []
    for line in raw.split("\\n"):
        if "||" not in line:
            continue
        u, lab = line.split("||", 1)
        if u.startswith("http"):
            out.append((u, lab))
    return out


def discover_url(root: str, year: int) -> str | None:
    """Landing-page fallback: find a likely nominees / winners URL."""
    ab("open", root, timeout=90)
    ab("wait", "--load", "networkidle", timeout=60)
    year_str = str(year)
    candidates = _hrefs_by_keyword(["nominat", "winner", "selection", "awards", year_str])
    if not candidates:
        return None

    def score(pair: tuple[str, str]) -> int:
        u, lab = pair
        s = 0
        low = (u + " " + lab).lower()
        if year_str in low:
            s += 5
        if "nominat" in low:
            s += 3
        if "winner" in low:
            s += 3
        if "selection" in low or "award" in low:
            s += 1
        return s

    candidates.sort(key=score, reverse=True)
    return candidates[0][0] if score(candidates[0]) > 0 else None


# ---------------- Wikipedia (verify) ----------------


def wiki_find_page(org: dict, year: int) -> str | None:
    name = org["name_en"]
    r = httpx.get(
        WIKI_API,
        params={
            "action": "query",
            "list": "search",
            "srsearch": f"{name} {year}",
            "format": "json",
            "srlimit": 10,
        },
        headers=WIKI_HEADERS,
        timeout=15,
    )
    hits = r.json().get("query", {}).get("search", [])
    name_tokens = {t.lower() for t in re.findall(r"[A-Za-z]+", name) if len(t) > 2}
    year_str = str(year)

    shortlist: list[str] = []
    for h in hits:
        title = h["title"]
        title_tokens = {t.lower() for t in re.findall(r"[A-Za-z]+", title)}
        if not (name_tokens & title_tokens):
            continue
        if ORDINAL_RE.match(title) or title.startswith(year_str):
            shortlist.append(title)

    for title in shortlist:
        if year_str in wiki_intro(title):
            return title
    return shortlist[0] if shortlist else (hits[0]["title"] if hits else None)


def wiki_intro(title: str) -> str:
    r = httpx.get(
        WIKI_API,
        params={
            "action": "query",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "titles": title,
            "format": "json",
            "redirects": 1,
        },
        headers=WIKI_HEADERS,
        timeout=15,
    )
    pages = r.json().get("query", {}).get("pages", {})
    for p in pages.values():
        return p.get("extract", "") or ""
    return ""


def wiki_fetch_text(title: str, host: str = "en.wikipedia.org") -> str:
    r = httpx.get(
        _wiki_api_from_host(host),
        params={
            "action": "parse",
            "page": title,
            "prop": "wikitext",
            "format": "json",
            "redirects": 1,
        },
        headers=WIKI_HEADERS,
        timeout=25,
    )
    data = r.json()
    if "parse" not in data:
        return ""
    return data["parse"]["wikitext"]["*"]


# ---------------- Gemini extractor ----------------

EXTRACTOR_SYSTEM = """You extract award nominees from the given text.

Rules:
- Output ONLY a JSON array matching the supplied schema. No prose.
- Each element: category, film_title_primary, film_title_alt (empty if none),
  person (empty if not a person category), result ("won" | "nominated").
- Include every category present; list ALL nominees per category; winner(s)
  result="won", rest result="nominated".
- film_title_primary: film/series title in the language used by the source.
  Put any parallel translation in film_title_alt.
- Skip honorary / lifetime / in-memoriam / governors / tribute sections.
- Return [] if the text has no nominee list (landing page, paywall, etc.).
"""


def call_gemini(system: str, user: str) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY missing")
    url = f"{settings.gemini_api_base}/models/{MODEL}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseSchema": NOMINEE_SCHEMA,
        },
    }
    with httpx.Client(timeout=300.0) as c:
        r = c.post(url, params={"key": settings.gemini_api_key}, json=payload)
        r.raise_for_status()
        data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"gemini response missing text: {data}") from e


def extract(text: str, header: str) -> list[dict]:
    trimmed = text[:EXTRACT_CHAR_CAP]
    user = f"{header}\n\nPage text follows.\n\n{trimmed}"
    raw = call_gemini(EXTRACTOR_SYSTEM, user)
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError(f"no JSON array in response: {raw[:400]}")
    return json.loads(raw[start : end + 1])


# ---------------- Verify (diff primary vs wiki) ----------------


def _title_key(n: dict) -> tuple[str, str]:
    t = (n.get("film_title_primary") or "").strip().lower()
    p = (n.get("person") or "").strip().lower()
    return (t, p)


def verify(primary: list[dict], wiki: list[dict]) -> None:
    p_keys = {_title_key(n) for n in primary}
    w_keys = {_title_key(n) for n in wiki}
    p_cats = {n["category"] for n in primary}
    w_cats = {n["category"] for n in wiki}

    print(f"  [verify] primary={len(primary)} wiki={len(wiki)}")
    print(f"  [verify] categories primary={len(p_cats)} wiki={len(w_cats)}")
    missing = w_keys - p_keys
    extras = p_keys - w_keys
    if missing:
        print(f"  [verify] {len(missing)} nominee(s) in wiki but NOT in official:")
        for t, person in sorted(missing)[:10]:
            print(f"    - {t!r} / {person!r}")
    if extras:
        print(f"  [verify] {len(extras)} nominee(s) in official but NOT in wiki:")
        for t, person in sorted(extras)[:10]:
            print(f"    - {t!r} / {person!r}")
    if not missing and not extras:
        print("  [verify] ✓ primary and wiki agree")


# ---------------- Ingest ----------------


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _wiki_title_from_url(u: str) -> tuple[str, str]:
    """Returns (title, host). Host defaults to en.wikipedia.org if URL has none."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(u)
    host = parsed.netloc or "en.wikipedia.org"
    path = parsed.path
    if "/wiki/" in path:
        return unquote(path.split("/wiki/", 1)[1]), host
    return u, host


def ingest(
    org_id: str,
    year: int,
    url_override: str | None,
    do_verify: bool,
    wiki_url: str | None = None,
) -> None:
    org = get_org(org_id)
    header = (
        f"Award: {org['name_en']} / {org.get('name_zh', '')} — year {year}\n"
        f"Region: {org.get('region', '')}  Scope: {org.get('scope', '')}"
    )

    if wiki_url:
        title, host = _wiki_title_from_url(wiki_url)
        print(f"=== {org['name_en']} {year} ===")
        print(f"  wiki fallback: {title} ({host})")
        wtext = wiki_fetch_text(title, host=host)
        print(f"  wiki text: {len(wtext)} chars → Gemini")
        primary = extract(wtext, header + f"\nWiki page: {title}") if wtext else []
        print(f"  primary nominees: {len(primary)}")
        url = f"wiki:{host}/{title}"
    else:
        url = resolve_url(org, year, url_override)
        print(f"=== {org['name_en']} {year} ===")
        print(f"  primary url: {url}")
        text = fetch_page_text(url)
        print(f"  primary text: {len(text)} chars → Gemini")
        primary = extract(text, header + f"\nSource URL: {url}") if len(text) >= 500 else []
        print(f"  primary nominees: {len(primary)}")

        if not primary:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            root = f"{parsed.scheme}://{parsed.netloc}/"
            print(f"  primary empty → discover on {root}")
            discovered = discover_url(root, year)
            if discovered and discovered != url:
                print(f"  discovered: {discovered}")
                url = discovered
                text = fetch_page_text(url)
                print(f"  primary text (retry): {len(text)} chars → Gemini")
                primary = extract(text, header + f"\nSource URL: {url}") if len(text) >= 500 else []
                print(f"  primary nominees (retry): {len(primary)}")

    if do_verify and not wiki_url:
        try:
            wtitle = wiki_find_page(org, year)
            if wtitle:
                print(f"  wiki page: {wtitle}")
                wtext = wiki_fetch_text(wtitle)
                wiki_nominees = extract(wtext, header + f"\nWiki page: {wtitle}") if wtext else []
                print(f"  wiki nominees: {len(wiki_nominees)}")
                verify(primary, wiki_nominees)
            else:
                print("  [verify] no wikipedia page found, skipping")
        except Exception as e:
            print(f"  [verify] skipped: {e}")

    if not primary:
        print("  (nothing to insert — empty primary)")
        return

    matched = 0
    with get_db() as conn:
        for i, n in enumerate(primary, 1):
            out = record_nomination(
                conn,
                org=org,
                year=year,
                category=n["category"],
                primary_title=n["film_title_primary"],
                alt_title=_clean(n.get("film_title_alt")),
                person=_clean(n.get("person")),
                result=n.get("result", "nominated"),
                source_url=url,
            )
            if out["matched_film_id"]:
                matched += 1
            if i % 20 == 0 or i == len(primary):
                print(f"    [{i}/{len(primary)}] matched so far: {matched}")
    print(f"=== Done: matched={matched}/{len(primary)} ===")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True, help="org_id from awards-registry.json")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--url", help="Override source URL", default=None)
    p.add_argument("--no-verify", action="store_true", help="Skip wiki cross-check")
    p.add_argument(
        "--wiki-url",
        default=None,
        help="Use wiki page as primary source (fallback when official blocked / SPA).",
    )
    args = p.parse_args()
    ingest(
        args.org,
        args.year,
        args.url,
        do_verify=not args.no_verify,
        wiki_url=args.wiki_url,
    )


if __name__ == "__main__":
    main()
