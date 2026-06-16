"""Additional unit tests for backend/tmdb_lookup.py (HTTP layer mocked).

Complements test_tmdb_lookup.py (which covers search_tmdb year/similarity
guards) by exercising catchplay_poster, fetch_tmdb_by_id, _format_tmdb_item
branches, and the no-key / empty-title guards.
"""

from unittest.mock import patch

import httpx

from backend import tmdb_lookup as tl


class _FakeResp:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    """httpx.Client stand-in usable as a context manager."""

    def __init__(self, resp=None, raises=None):
        self._resp = resp
        self._raises = raises

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **kw):
        if self._raises:
            raise self._raises
        return self._resp


def _patch_client(resp=None, raises=None):
    return patch.object(tl.httpx, "Client", return_value=_FakeClient(resp=resp, raises=raises))


def _search_resp(items):
    return _FakeResp(200, payload={"results": items})


# ── og_image (poster fetch) ──────────────────────────────────────────────────
# og_image extraction is tested with _safe_get patched (the SSRF-guarded fetch
# is covered separately below); catchplay_poster is a back-compat alias.


def _patch_fetch(resp=None):
    return patch.object(tl, "_safe_get", return_value=resp)


def test_og_image_none_url():
    assert tl.og_image(None) is None


def test_catchplay_poster_is_alias():
    assert tl.catchplay_poster is tl.og_image


def test_og_image_extracts():
    html = '<meta property="og:image" content="https://img.cdn/poster.jpg" />'
    with _patch_fetch(_FakeResp(200, html)):
        assert tl.og_image("https://example.com/x") == "https://img.cdn/poster.jpg"


def test_og_image_non_200():
    with _patch_fetch(_FakeResp(404, "nope")):
        assert tl.og_image("https://example.com/x") is None


def test_og_image_blocked_url_returns_none():
    # _safe_get returns None when the URL is unsafe (private host / bad scheme).
    with _patch_fetch(None):
        assert tl.og_image("http://10.0.0.1/x") is None


def test_og_image_no_meta_tag():
    with _patch_fetch(_FakeResp(200, "<html>no meta</html>")):
        assert tl.og_image("https://example.com/x") is None


def test_og_image_rejects_global_landing_logo():
    html = '<meta property="og:image" content="https://img/global-landing-logo.png">'
    with _patch_fetch(_FakeResp(200, html)):
        assert tl.og_image("https://example.com/x") is None


def test_og_image_rejects_events_logo():
    html = '<meta property="og:image" content="https://img/events/promo.png">'
    with _patch_fetch(_FakeResp(200, html)):
        assert tl.og_image("https://example.com/x") is None


def test_og_image_swallows_http_error():
    with patch.object(tl, "_safe_get", side_effect=httpx.HTTPError("boom")):
        assert tl.og_image("https://example.com/x") is None


# ── SSRF guards ──────────────────────────────────────────────────────────────


def _addrinfo(ip):
    return [(2, 1, 6, "", (ip, 0))]


def test_is_safe_url_rejects_non_http():
    assert tl._is_safe_url("file:///etc/passwd") is False
    assert tl._is_safe_url("ftp://host/x") is False
    assert tl._is_safe_url("gopher://host") is False


def test_is_safe_url_rejects_no_host():
    assert tl._is_safe_url("https://") is False


def test_host_is_public_blocks_internal_ranges():
    import socket as _socket

    for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.5", "169.254.169.254", "::1"):
        with patch.object(tl.socket, "getaddrinfo", return_value=_addrinfo(ip)):
            assert tl._host_is_public("evil.test") is False, ip
    with patch.object(tl.socket, "getaddrinfo", side_effect=_socket.gaierror):
        assert tl._host_is_public("nxdomain.test") is False


def test_host_is_public_allows_public_ip():
    with patch.object(tl.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert tl._host_is_public("example.com") is True


class _RedirectResp:
    is_redirect = True
    has_redirect_location = True

    def __init__(self, location):
        self.next_request = type("Req", (), {"url": location})()


class _SeqClient:
    """httpx.Client stand-in returning queued responses in order."""

    def __init__(self, resps):
        self._resps = list(resps)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        return self._resps.pop(0)


def test_safe_get_follows_safe_redirect():
    final = _FakeResp(200, "ok")
    final.is_redirect = False
    final.has_redirect_location = False
    seq = _SeqClient([_RedirectResp("https://cdn.example.com/final"), final])
    with (
        patch.object(tl, "_host_is_public", return_value=True),
        patch.object(tl.httpx, "Client", return_value=seq),
    ):
        assert tl._safe_get("https://example.com/x").text == "ok"


def test_safe_get_blocks_redirect_into_private():
    seq = _SeqClient([_RedirectResp("http://169.254.169.254/latest/meta-data")])
    with (
        patch.object(tl, "_host_is_public", side_effect=lambda h: h != "169.254.169.254"),
        patch.object(tl.httpx, "Client", return_value=seq),
    ):
        assert tl._safe_get("https://example.com/x") is None


# ── _format_tmdb_item ────────────────────────────────────────────────────────


def test_format_movie_item():
    item = {
        "media_type": "movie",
        "id": 42,
        "title": "寄生上流",
        "original_title": "Parasite",
        "release_date": "2019-05-30",
        "poster_path": "/p.jpg",
        "backdrop_path": "/b.jpg",
        "overview": "...",
        "vote_average": 8.5,
    }
    out = tl._format_tmdb_item(item)
    assert out["tmdb_id"] == 42
    assert out["tmdb_media_type"] == "movie"
    assert out["tmdb_title"] == "寄生上流"
    assert out["tmdb_original_title"] == "Parasite"
    assert out["tmdb_year"] == 2019
    assert out["tmdb_poster_url"].endswith("/p.jpg")
    assert out["tmdb_backdrop_url"].endswith("/b.jpg")


def test_format_tv_item_uses_name_fields():
    item = {
        "media_type": "tv",
        "id": 7,
        "name": "魷魚遊戲",
        "original_name": "Squid Game",
        "first_air_date": "2021-09-17",
    }
    out = tl._format_tmdb_item(item)
    assert out["tmdb_media_type"] == "tv"
    assert out["tmdb_title"] == "魷魚遊戲"
    assert out["tmdb_original_title"] == "Squid Game"
    assert out["tmdb_year"] == 2021
    assert out["tmdb_poster_url"] is None  # no poster_path
    assert out["tmdb_backdrop_url"] is None


def test_format_item_infers_media_type_from_title_key():
    # no media_type but has "title" → inferred movie
    out = tl._format_tmdb_item({"id": 1, "title": "X"})
    assert out["tmdb_media_type"] == "movie"


def test_format_item_infers_tv_when_no_title():
    out = tl._format_tmdb_item({"id": 1, "name": "Y"})
    assert out["tmdb_media_type"] == "tv"


def test_format_item_non_digit_year_is_none():
    out = tl._format_tmdb_item({"media_type": "movie", "id": 1, "title": "X", "release_date": ""})
    assert out["tmdb_year"] is None


# ── fetch_tmdb_by_id ─────────────────────────────────────────────────────────


def test_fetch_tmdb_by_id_no_key(monkeypatch):
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "", raising=False)
    assert tl.fetch_tmdb_by_id(42) is None


def test_fetch_tmdb_by_id_zero_id(monkeypatch):
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "k", raising=False)
    assert tl.fetch_tmdb_by_id(0) is None


def test_fetch_tmdb_by_id_success(monkeypatch):
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "k", raising=False)
    payload = {
        "id": 42,
        "title": "寄生上流",
        "original_title": "Parasite",
        "release_date": "2019-05-30",
    }
    with _patch_client(resp=_FakeResp(200, payload=payload)):
        out = tl.fetch_tmdb_by_id(42, media_type="movie")
    assert out["tmdb_id"] == 42
    assert out["tmdb_media_type"] == "movie"
    assert out["tmdb_year"] == 2019


def test_fetch_tmdb_by_id_non_200(monkeypatch):
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "k", raising=False)
    with _patch_client(resp=_FakeResp(500)):
        assert tl.fetch_tmdb_by_id(42) is None


# ── search_tmdb guards (not covered by existing file) ────────────────────────


def test_search_tmdb_no_key(monkeypatch):
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "", raising=False)
    assert tl.search_tmdb("anything") is None


def test_search_tmdb_empty_title(monkeypatch):
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "k", raising=False)
    assert tl.search_tmdb("") is None


def test_search_tmdb_non_200(monkeypatch):
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "k", raising=False)
    with _patch_client(resp=_FakeResp(503)):
        assert tl.search_tmdb("寄生上流") is None


def test_search_tmdb_success_returns_best_match(monkeypatch):
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "k", raising=False)
    items = [
        {  # filtered: not movie/tv
            "media_type": "person",
            "id": 1,
            "name": "Bong Joon-ho",
        },
        {
            "media_type": "movie",
            "id": 496243,
            "title": "Parasite",
            "original_title": "Parasite",
            "release_date": "2019-05-30",
            "poster_path": "/p.jpg",
        },
    ]
    with _patch_client(resp=_search_resp(items)):
        out = tl.search_tmdb("Parasite")
    assert out is not None
    assert out["tmdb_id"] == 496243


def test_search_tmdb_year_window_skips_then_accepts(monkeypatch):
    """Candidate outside ±2 release_year is skipped; next valid one returned."""
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "k", raising=False)
    items = [
        {
            "media_type": "movie",
            "id": 1,
            "title": "Parasite",
            "original_title": "Parasite",
            "release_date": "2010-01-01",  # too old for release_year=2019
        },
        {
            "media_type": "movie",
            "id": 2,
            "title": "Parasite",
            "original_title": "Parasite",
            "release_date": "2019-05-30",
        },
    ]
    with _patch_client(resp=_search_resp(items)):
        out = tl.search_tmdb("Parasite", release_year=2019)
    assert out["tmdb_id"] == 2


def test_search_tmdb_no_results_returns_none(monkeypatch):
    monkeypatch.setattr(tl.settings, "tmdb_api_key", "k", raising=False)
    with _patch_client(resp=_search_resp([])):
        assert tl.search_tmdb("Parasite") is None
