"""Minimal i18n for the NiceGUI frontend.

Locale tables live in `frontend.locales`, keyed by dotted namespaces
(`nav.search`, `autotag.title`, ...). `t(key, **kwargs)` looks up the current
locale, falls back to the default locale, then to the key itself (so a missing
string is visible, not blank). `**kwargs` are str.format-ed in.
"""

from __future__ import annotations

import json
import os
from importlib import resources

DEFAULT_LOCALE = "zh_TW"
_current = DEFAULT_LOCALE

# Brand name stamped on library-ownership labels ("{brand} 有此片", "在 {brand}
# 觀看"). Defaults to CATCHPLAY+ for the hackathon demo; override with the
# BRAND_NAME env var to rebrand the UI without touching locale tables. Any
# locale string containing `{brand}` gets this injected automatically (callers
# need not pass it) — see `t()`.
BRAND_NAME = os.getenv("BRAND_NAME", "CATCHPLAY+")

_LOCALE_FILES = {
    "zh_TW": "zh_TW.json",
}


def _load_locale(locale: str) -> dict[str, str]:
    filename = _LOCALE_FILES[locale]
    source = resources.files("frontend.locales").joinpath(filename)
    return json.loads(source.read_text(encoding="utf-8"))


TRANSLATIONS: dict[str, dict[str, str]] = {locale: _load_locale(locale) for locale in _LOCALE_FILES}


def set_locale(locale: str) -> None:
    global _current
    _current = locale


def get_locale() -> str:
    return _current


def t(key: str, **kwargs) -> str:
    table = TRANSLATIONS.get(_current, {})
    s = table.get(key)
    if s is None:
        s = TRANSLATIONS[DEFAULT_LOCALE].get(key, key)
    # Auto-supply the brand for any string carrying a `{brand}` placeholder, so
    # rebranding is a single env var, not edits at every call site.
    if "{brand}" in s and "brand" not in kwargs:
        kwargs["brand"] = BRAND_NAME
    return s.format(**kwargs) if kwargs else s
