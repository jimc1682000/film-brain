"""Unit tests for LLM query expansion (mocked LLM + fake registry)."""

import json

import pytest

from backend.services import query_expand as qe


class _FakeLLM:
    """LLMClient Protocol double wrapping a call function (ADR 0021 seam)."""

    def __init__(self, fn):
        self._fn = fn

    def call_llm(self, *a, **k):
        return self._fn(*a, **k)


class _FakeRegistry:
    """Minimal registry: 'romance'/'korean' are real; everything else invalid."""

    _DIM = {"romance": "genre", "korean": "region"}

    def to_prompt_context(self):
        return "TAGS: romance(愛情), korean(韓國)"

    def validate_tag_ids(self, ids):
        valid = [t for t in ids if t in self._DIM]
        return valid, [t for t in ids if t not in self._DIM]

    _LABEL = {"romance": "羅曼史", "korean": "韓國"}

    def get_tag(self, tid):
        if tid not in self._DIM:
            return None
        return {
            "tag_id": tid,
            "dimension": self._DIM[tid],
            "labels": {"zh_TW": self._LABEL.get(tid, "")},
        }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    qe._cache.clear()
    monkeypatch.setattr(qe, "_registry", _FakeRegistry())
    yield
    qe._cache.clear()


def test_valid_expansion_groups_and_drops_hallucinated(monkeypatch):
    payload = json.dumps(
        {
            "tags": ["romance", "korean", "not-a-real-tag"],
            "hyde": "一段愛情劇情",
            "keywords": ["愛情", "韓國", ""],
        }
    )
    out = qe.expand_query("韓國愛情片", llm_client=_FakeLLM(lambda *a, **k: payload))
    # All dims are boost now (no hard filters) → filters always empty; every
    # valid tag becomes a weighted boost_tag; the fake tag is dropped.
    assert out["filters"] == {}
    assert out["hyde_text"] == "一段愛情劇情"
    bt = {tid for tid, _w in out["boost_tags"]}
    assert "korean" in bt and "romance" in bt  # both real dims → boost
    assert "not-a-real-tag" not in bt
    assert "愛情" in out["keywords"]


def test_fallback_on_llm_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("429")

    out = qe.expand_query("隨便", llm_client=_FakeLLM(_boom))
    # Empty expansion, flagged degraded so callers skip caching it.
    assert out == {
        "filters": {},
        "hyde_text": "",
        "keywords": [],
        "boost_tags": [],
        "stepback_text": "",
        "award_presence": False,
        "_degraded": True,
    }
    # A degraded result is NOT cached → retried once the LLM recovers.
    assert "隨便" not in qe._cache


def test_cache_avoids_second_call(monkeypatch):
    calls = {"n": 0}

    def _once(*a, **k):
        calls["n"] += 1
        return json.dumps({"tags": ["romance"], "hyde": "x", "keywords": ["愛情"]})

    fake = _FakeLLM(_once)
    qe.expand_query("愛情", llm_client=fake)
    qe.expand_query("愛情", llm_client=fake)
    assert calls["n"] == 1


def test_empty_query_skips_llm():
    fake = _FakeLLM(lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert qe.expand_query("  ", llm_client=fake) == {
        "filters": {},
        "hyde_text": "",
        "keywords": [],
        "boost_tags": [],
        "stepback_text": "",
        "award_presence": False,
    }


def test_award_presence_flag(monkeypatch):
    # Generic "award-winning" query → LLM sets award_presence (folded in from the
    # old regex parser's _require_award_presence).
    payload = json.dumps(
        {"tags": [], "hyde": "一部得獎電影", "keywords": ["得獎"], "award_presence": True}
    )
    out = qe.expand_query("奧斯卡得獎電影", llm_client=_FakeLLM(lambda *a, **k: payload))
    assert out["award_presence"] is True


def test_award_presence_defaults_false():
    payload = json.dumps({"tags": ["romance"], "hyde": "x", "keywords": ["愛情"]})
    out = qe.expand_query("愛情片", llm_client=_FakeLLM(lambda *a, **k: payload))
    assert out["award_presence"] is False
