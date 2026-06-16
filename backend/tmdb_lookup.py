"""Minimal sync TMDB lookup used by award-tracker ingest."""

import ipaddress
import re
import socket
from difflib import SequenceMatcher
from urllib.parse import urlparse

import httpx

from backend.config import settings

TMDB_BASE = "https://api.themoviedb.org/3"
POSTER_PREFIX = "https://image.tmdb.org/t/p/w500"
# Wide cinematic still for the MUBI-style detail hero (16:9, ~1280px).
BACKDROP_PREFIX = "https://image.tmdb.org/t/p/w1280"

_OG_IMAGE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', re.IGNORECASE)

# Max redirect hops to follow when fetching an og:image page. Each hop is
# re-validated (see _safe_get) so a public URL can't bounce us into the
# internal network.
_MAX_REDIRECTS = 3


def _host_is_public(host: str) -> bool:
    """True only if every IP `host` resolves to is a public address.

    Blocks the SSRF-relevant ranges: loopback, RFC1918 private, link-local
    (incl. the 169.254.169.254 cloud-metadata endpoint), reserved, multicast,
    and unspecified. DNS-rebinding (resolve-then-reconnect) is out of scope —
    this app is meant to run local/trusted (see SECURITY.md).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _is_safe_url(url: str) -> bool:
    """Reject non-http(s) schemes and hosts that resolve to private/internal IPs."""
    parsed = urlparse(url)
    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.hostname)
        and _host_is_public(parsed.hostname)
    )


def _safe_get(url: str) -> httpx.Response | None:
    """GET `url` with SSRF guards: validate scheme + host on every hop, and
    follow redirects manually so a public URL can't redirect into the internal
    network (httpx's follow_redirects would). None if any hop is unsafe."""
    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            if not _is_safe_url(url):
                return None
            resp = client.get(url)
            if resp.is_redirect and resp.next_request is not None:
                url = str(resp.next_request.url)
                continue
            return resp
    return None


def og_image(url: str | None) -> str | None:
    """Read the og:image off any film/source web page. None on any failure.

    Source-neutral: works for any poster page, not just one catalogue. The URL
    is user-supplied (e.g. the create-film API), so the fetch is SSRF-guarded —
    only http(s) to public hosts, redirects validated per hop (see _safe_get).

    The `global-landing` / `events` filter rejects CATCHPLAY+'s geo-redirect
    landing page (out-of-TW IPs get a generic logo, not the film poster) — a
    harmless no-op for other sources.
    """
    if not url:
        return None
    try:
        r = _safe_get(url)
        if r is None or r.status_code != 200:
            return None
        m = _OG_IMAGE.search(r.text)
        poster = m.group(1) if m else None
        if poster and ("global-landing" in poster or "/events/" in poster):
            return None  # geo-gate generic logo, not the film poster
        return poster
    except httpx.HTTPError:
        return None


# Back-compat alias — older callers import catchplay_poster.
catchplay_poster = og_image


# Minimum normalised-title similarity for a TMDB candidate to be accepted when
# no authoritative tmdb_id is available. Tuned to reject anime/series sharing
# a substring with the real film (e.g. ICHU 偶像進行曲 vs 進行曲).
MIN_TITLE_SIMILARITY = 0.6


def _normalise(s: str) -> str:
    return "".join(s.lower().split())


def _format_tmdb_item(item: dict) -> dict:
    mt = item.get("media_type") or ("movie" if "title" in item else "tv")
    poster = item.get("poster_path") or ""
    backdrop = item.get("backdrop_path") or ""
    release = item.get("release_date") or item.get("first_air_date") or ""
    yr: int | None = int(release[:4]) if release[:4].isdigit() else None
    return {
        "tmdb_id": item.get("id"),
        "tmdb_media_type": mt,
        "tmdb_title": item.get("title") if mt == "movie" else item.get("name"),
        "tmdb_original_title": item.get("original_title")
        if mt == "movie"
        else item.get("original_name"),
        "tmdb_year": yr,
        "tmdb_poster_url": f"{POSTER_PREFIX}{poster}" if poster else None,
        "tmdb_backdrop_url": f"{BACKDROP_PREFIX}{backdrop}" if backdrop else None,
        "tmdb_overview": item.get("overview") or "",
        "tmdb_vote_avg": item.get("vote_average"),
    }


def fetch_tmdb_by_id(tmdb_id: int, media_type: str = "movie", lang: str = "zh-TW") -> dict | None:
    """Authoritative TMDB lookup when the id is already known.

    Preferred over `search_tmdb` whenever the CATCHPLAY+ film row already has a
    verified `tmdb_id` — avoids the fuzzy-name collisions (e.g. 進行曲 picking
    the ICHU anime by popularity) that pollute `award_nominees`.
    """
    if not settings.tmdb_api_key or not tmdb_id:
        return None
    with httpx.Client(timeout=15.0) as c:
        r = c.get(
            f"{TMDB_BASE}/{media_type}/{tmdb_id}",
            params={"api_key": settings.tmdb_api_key, "language": lang},
        )
        if r.status_code != 200:
            return None
        item = r.json()
    item.setdefault("media_type", media_type)
    return _format_tmdb_item(item)


def search_tmdb(
    title: str,
    lang: str = "zh-TW",
    *,
    release_year: int | None = None,
    min_similarity: float = MIN_TITLE_SIMILARITY,
) -> dict | None:
    """Title search with optional release-year / similarity gating.

    `release_year` is the CATCHPLAY+ film release year (not the ceremony year).
    When provided, candidates outside ±2 are dropped. `min_similarity` rejects
    weak partial matches even if popularity ranks them first.
    """
    if not settings.tmdb_api_key or not title:
        return None
    with httpx.Client(timeout=15.0) as c:
        r = c.get(
            f"{TMDB_BASE}/search/multi",
            params={"api_key": settings.tmdb_api_key, "query": title, "language": lang},
        )
        if r.status_code != 200:
            return None
        results = r.json().get("results", []) or []

    candidates = [i for i in results if i.get("media_type") in ("movie", "tv")]
    query_norm = _normalise(title)

    for item in candidates:
        formatted = _format_tmdb_item(item)
        if release_year is not None and formatted["tmdb_year"] is not None:
            if abs(formatted["tmdb_year"] - release_year) > 2:
                continue
        # Compare against TMDB title in both display + original form.
        best_sim = 0.0
        for cand_title in (formatted["tmdb_title"], formatted["tmdb_original_title"]):
            if not cand_title:
                continue
            sim = SequenceMatcher(None, query_norm, _normalise(cand_title)).ratio()
            best_sim = max(best_sim, sim)
        if best_sim < min_similarity:
            continue
        return formatted
    return None
