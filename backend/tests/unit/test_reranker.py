"""Unit tests for the cross-encoder reranker (CE model + DB mocked)."""

import contextlib
import sqlite3

import numpy as np
import pytest

from backend.db import init_db
from backend.services import reranker as rr
from backend.tests.fixtures.mock_films import seed_mock_db


class _FakeCE:
    """Stand-in CrossEncoder: score = length of the doc text (deterministic)."""

    def predict(self, pairs, show_progress_bar=False):
        return np.array([float(len(doc)) for _q, doc in pairs])


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setattr(rr, "_get_model", lambda: _FakeCE())


# ── _doc_text (pure) ────────────────────────────────────────────────────────


def test_doc_text_full():
    txt = rr._doc_text(
        {"title_zh": "星界航線", "title_en": "Starline", "tags": ["sci-fi"]},
        {
            "year": 2024,
            "country": "US",
            "director": "A. Director",
            "cast": "Someone",
            "desc": "深太空",
        },
    )
    assert "星界航線 / Starline (2024) US" in txt
    assert "標籤: sci-fi" in txt and "導演:" in txt and "劇情: 深太空" in txt


def test_doc_text_minimal():
    assert rr._doc_text({"title_zh": "X"}) == "X"


def test_doc_text_en_only_no_title_zh():
    txt = rr._doc_text({"title_en": "Solo"}, {"year": 2020})
    assert "Solo (2020)" in txt


# ── rerank_with_cross_encoder ───────────────────────────────────────────────


def _cands():
    return [
        {
            "film_id": "mock-001",
            "title_zh": "短",
            "tags": ["comedy"],
            "rrf_score": 0.9,
            "_pre_ce_rank": 0,
        },
        {
            "film_id": "mock-002",
            "title_zh": "比較長的標題敘述",
            "tags": ["thriller"],
            "rrf_score": 0.5,
            "_pre_ce_rank": 5,
        },
        {
            "film_id": "mock-010",
            "title_zh": "最長最長最長的那一個標題",
            "tags": ["sci-fi", "action"],
            "rrf_score": 0.1,
            "_pre_ce_rank": 12,
        },
    ]


def test_rerank_empty_returns_input(fake_model):
    assert rr.rerank_with_cross_encoder("q", []) == []


def test_rerank_reorders_and_scores(monkeypatch, fake_model):
    monkeypatch.setattr(rr, "_fetch_meta", lambda ids: {})
    out = rr.rerank_with_cross_encoder("好片", _cands())
    assert out is not None and len(out) == 3
    # every result carries a normalized blended display score + ce logit
    for c in out:
        assert "llm_score" in c and "ce_logit" in c and "ce_blend_w" in c
    # sorted desc by llm_score
    scores = [c["llm_score"] for c in out]
    assert scores == sorted(scores, reverse=True)
    # pre_ce_rank drives the blend weight (1-3→0.25, 4-10→0.4, 11+→0.6)
    by_id = {c["film_id"]: c for c in out}
    assert by_id["mock-001"]["ce_blend_w"] == 0.25
    assert by_id["mock-002"]["ce_blend_w"] == 0.4
    assert by_id["mock-010"]["ce_blend_w"] == 0.6


def test_rerank_model_load_fail_returns_none(monkeypatch):
    def _boom():
        raise RuntimeError("no model")

    monkeypatch.setattr(rr, "_get_model", _boom)
    monkeypatch.setattr(rr, "_fetch_meta", lambda ids: {})
    assert rr.rerank_with_cross_encoder("q", _cands()) is None


def test_warmup_success(fake_model):
    assert rr.warmup() is True


def test_warmup_failure(monkeypatch):
    monkeypatch.setattr(rr, "_get_model", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert rr.warmup() is False


# ── _fetch_meta (seeded in-memory DB) ───────────────────────────────────────


def test_fetch_meta(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    seed_mock_db(conn)
    conn.close()  # only used to seed; _fetch_meta opens its own via _fake_get_db

    @contextlib.contextmanager
    def _fake_get_db():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    monkeypatch.setattr(rr, "get_db", _fake_get_db)
    meta = rr._fetch_meta(["mock-001", "mock-004"])
    assert set(meta) == {"mock-001", "mock-004"}
    assert "喜劇" in meta["mock-001"]["desc"] or meta["mock-001"]["desc"]
    assert rr._fetch_meta([]) == {}


# ── CrossEncoderReranker adapter / provider (ADR 0021) ──────────────────────


def test_adapter_delegates_to_function(monkeypatch):
    """The adapter's rerank() forwards verbatim to rerank_with_cross_encoder."""
    seen = {}

    def _fake(query, candidates):
        seen["args"] = (query, candidates)
        return ["sentinel"]

    monkeypatch.setattr(rr, "rerank_with_cross_encoder", _fake)
    out = rr.CrossEncoderReranker().rerank("q", [{"film_id": "x"}])
    assert out == ["sentinel"]
    assert seen["args"] == ("q", [{"film_id": "x"}])


def test_adapter_satisfies_protocol():
    from backend.interfaces import Reranker

    assert isinstance(rr.CrossEncoderReranker(), Reranker)


def test_get_reranker_is_singleton():
    assert rr.get_reranker() is rr.get_reranker()
    assert isinstance(rr.get_reranker(), rr.CrossEncoderReranker)
