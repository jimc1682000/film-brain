"""Unit tests for EmbedService (backend mocked with deterministic fake_embed)."""

import pytest

from backend.config import settings
from backend.services.embedder import EmbedService
from backend.tests.fixtures.mock_films import fake_embed


@pytest.fixture
def embed(monkeypatch):
    """EmbedService whose backend is the deterministic fake (no model/daemon)."""
    monkeypatch.setattr(settings, "embedding_backend", "ollama", raising=False)
    monkeypatch.setattr(EmbedService, "_embed_ollama", lambda self, texts: fake_embed(texts))
    return EmbedService()


# ── build_film_text (pure) ──────────────────────────────────────────────────


def test_build_film_text_combines_fields():
    text = EmbedService.build_film_text(
        {"title_zh": "星界航線", "title_en": "Starline", "description": "深太空 AI 甦醒"}
    )
    assert "星界航線" in text and "Starline" in text and "深太空 AI 甦醒" in text
    assert " | " in text


def test_build_film_text_includes_tags_and_tmdb():
    text = EmbedService.build_film_text(
        {
            "title_zh": "機械叛變",
            "tag_labels": ["科幻", "動作"],
            "tmdb_overview": "robots revolt",
            "tmdb_genres": '["Sci-Fi", "Action"]',
            "tmdb_keywords": ["dystopia"],
        }
    )
    assert "Tags: 科幻, 動作" in text
    assert "robots revolt" in text
    assert "Sci-Fi" in text and "dystopia" in text


def test_build_film_text_tolerates_bad_json():
    # malformed tmdb_genres must not raise — just skipped
    text = EmbedService.build_film_text({"title_zh": "X", "tmdb_genres": "{not json"})
    assert text == "X"


def test_build_film_text_empty():
    assert EmbedService.build_film_text({}) == ""


# ── embed / embed_single ────────────────────────────────────────────────────


def test_embed_returns_deterministic_vectors(embed):
    v1 = embed.embed(["星界航線"])
    v2 = embed.embed(["星界航線"])
    assert v1 == v2  # reproducible
    assert len(v1) == 1 and len(v1[0]) == 1024


def test_embed_empty_list(embed):
    assert embed.embed([]) == []


def test_embed_single(embed):
    v = embed.embed_single("午夜來電")
    assert isinstance(v, list) and len(v) == 1024


def test_unknown_backend_raises(embed, monkeypatch):
    monkeypatch.setattr(settings, "embedding_backend", "bogus", raising=False)
    with pytest.raises(ValueError, match="unknown embedding_backend"):
        embed.embed(["x"])


def test_sentence_transformers_backend(monkeypatch):
    monkeypatch.setattr(settings, "embedding_backend", "sentence-transformers", raising=False)
    svc = EmbedService()
    monkeypatch.setattr(EmbedService, "_embed_st", lambda self, texts: fake_embed(texts))
    out = svc.embed(["abc"])
    assert len(out) == 1 and len(out[0]) == 1024


# ── warmup_tag_cache ────────────────────────────────────────────────────────


class _FakeRegistry:
    all_tag_ids = ["comedy", "thriller"]

    def get_tag(self, tid):
        return {"labels": {"zh_TW": "喜劇" if tid == "comedy" else "驚悚", "en": tid}}


def test_warmup_tag_cache(embed):
    n = embed.warmup_tag_cache(_FakeRegistry())
    assert n == 2
    assert set(embed.tag_vector_cache) == {"comedy", "thriller"}
    assert len(embed.tag_vector_cache["comedy"]) == 1024


def test_warmup_tag_cache_empty(embed):
    class _Empty:
        all_tag_ids: list = []

    assert embed.warmup_tag_cache(_Empty()) == 0


# ── execute (async) ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute(embed):
    out = await embed.execute({"texts": ["a", "b"]})
    assert len(out["embeddings"]) == 2
    assert out["dim"] == settings.embedding_dim
