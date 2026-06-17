"""search_config.get_config — nested-merge robustness (backend/services/search_config.py)."""

import json

import pytest

from backend.services import prompt_guard as pg
from backend.services import search_config as sc


@pytest.fixture(autouse=True)
def _restore_cache():
    saved = dict(sc._cache)
    yield
    sc._cache.clear()
    sc._cache.update(saved)


def _point_at(tmp_path, monkeypatch, payload):
    cfg_file = tmp_path / "search-config.json"
    cfg_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(sc, "_PATH", cfg_file)
    sc._cache.update(mtime=None, data=sc._DEFAULTS)


def test_partial_prompt_guard_override_keeps_defaults(tmp_path, monkeypatch):
    # Overriding only `block` must not drop the rest of the prompt_guard config.
    _point_at(tmp_path, monkeypatch, {"prompt_guard": {"block": 30}})
    pgc = sc.get_config()["prompt_guard"]
    assert pgc["block"] == 30  # override applied
    assert pgc["suspicious"] == 10  # sibling default kept
    assert pgc["weights"]["high"] == 35  # nested default kept
    assert "char_ratio_threshold" in pgc


def test_partial_weights_override_keeps_sibling_weights(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, {"prompt_guard": {"weights": {"high": 99}}})
    w = sc.get_config()["prompt_guard"]["weights"]
    assert w["high"] == 99  # override
    assert w["medium"] == 20  # sibling default kept


def test_inspect_survives_partial_override(tmp_path, monkeypatch):
    # The bug: a partial override made inspect() KeyError on every query.
    _point_at(tmp_path, monkeypatch, {"prompt_guard": {"block": 30}})
    assert pg.inspect("韓國犯罪驚悚片").level is pg.RiskLevel.SAFE
