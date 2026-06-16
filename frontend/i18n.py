"""Minimal i18n for the NiceGUI frontend.

Locale tables live in `frontend.locales`, keyed by dotted namespaces
(`nav.search`, `autotag.title`, ...). `t(key, **kwargs)` looks up the current
locale, falls back to the default locale, then to the key itself (so a missing
string is visible, not blank). `**kwargs` are str.format-ed in.
"""

from __future__ import annotations

import json
from importlib import resources

DEFAULT_LOCALE = "zh_TW"
_current = DEFAULT_LOCALE

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
    return s.format(**kwargs) if kwargs else s
