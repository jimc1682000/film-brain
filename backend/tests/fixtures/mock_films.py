"""Tiny, fully-synthetic film dataset for tests + the public demo.

No real catalogue, no external API, no CATCHPLAY data — just ~10 invented films
with valid taxonomy tag_ids. Used to exercise the full pipeline (import → tag →
embed → search) deterministically. `fake_embed` gives reproducible vectors so
search/rerank tests don't need the real embedding model or Qdrant.
"""

from __future__ import annotations

import hashlib
import math

from backend.poster import title_card_data_uri

# film_id, title_zh, title_en, description, original_genre, genre tag_ids
MOCK_FILMS: list[dict] = [
    {
        "film_id": "mock-001",
        "title_zh": "笑園驚魂夜",
        "title_en": "Laugh Manor",
        "description": "一群朋友受困鬧鬼莊園,卻把每場驚嚇都變成笑料的喜劇。",
        "original_genre": "喜劇",
        "tags": ["comedy"],
    },
    {
        "film_id": "mock-002",
        "title_zh": "午夜來電",
        "title_en": "Midnight Caller",
        "description": "刑警追查一連串只在午夜響起的神祕電話,真相步步逼近的驚悚片。",
        "original_genre": "驚悚",
        "tags": ["thriller"],
    },
    {
        "film_id": "mock-003",
        "title_zh": "雨季的告白",
        "title_en": "Confession in the Rain",
        "description": "兩個錯過十年的人在同一個雨季重逢,試著補回遺失時光的愛情故事。",
        "original_genre": "愛情",
        "tags": ["romance"],
    },
    {
        "film_id": "mock-004",
        "title_zh": "星界航線",
        "title_en": "Starline",
        "description": "一艘殖民船的 AI 在深太空甦醒,開始質疑任務目的的科幻作品。",
        "original_genre": "科幻",
        "tags": ["sci-fi"],
    },
    {
        "film_id": "mock-005",
        "title_zh": "無聲的閣樓",
        "title_en": "The Silent Attic",
        "description": "搬進老宅的一家人,發現閣樓裡有不該存在的腳步聲的恐怖片。",
        "original_genre": "恐怖",
        "tags": ["horror"],
    },
    {
        "film_id": "mock-006",
        "title_zh": "極速通緝",
        "title_en": "Full Throttle",
        "description": "退役賽車手被迫重返街頭,在一夜之間橫越全城的動作片。",
        "original_genre": "動作",
        "tags": ["action"],
    },
    {
        "film_id": "mock-007",
        "title_zh": "燈塔守候",
        "title_en": "The Lighthouse Keeper",
        "description": "獨守燈塔的老人與一封遲到四十年的信,關於原諒的劇情片。",
        "original_genre": "劇情",
        "tags": ["drama"],
    },
    {
        "film_id": "mock-008",
        "title_zh": "完美騙局",
        "title_en": "The Perfect Con",
        "description": "一群騙徒策劃最後一票,卻發現自己才是被設計的犯罪片。",
        "original_genre": "犯罪",
        "tags": ["crime"],
    },
    {
        "film_id": "mock-009",
        "title_zh": "婚禮逃兵",
        "title_en": "Runaway Vows",
        "description": "兩個在彼此婚禮上落跑的人意外同行,啼笑皆非又心動的愛情喜劇。",
        "original_genre": "喜劇",
        "tags": ["comedy", "romance"],
    },
    {
        "film_id": "mock-010",
        "title_zh": "機械叛變",
        "title_en": "Machine Uprising",
        "description": "近未來城市的維安機器人集體失控,工程師必須阻止災難的科幻動作片。",
        "original_genre": "科幻",
        "tags": ["sci-fi", "action"],
    },
]

# Minimal valid tags (dimension=genre). label_zh_tw kept simple.
MOCK_TAGS: list[dict] = [
    {
        "tag_id": t,
        "dimension": "genre",
        "label_en": t.title(),
        "label_zh_tw": zh,
        "source": "migrated",
        "status": "active",
    }
    for t, zh in [
        ("comedy", "喜劇"),
        ("thriller", "驚悚"),
        ("romance", "愛情"),
        ("sci-fi", "科幻"),
        ("horror", "恐怖"),
        ("action", "動作"),
        ("drama", "劇情"),
        ("crime", "犯罪"),
    ]
]

_DIM = 1024


def fake_embed(texts: list[str], dim: int = _DIM) -> list[list[float]]:
    """Deterministic, reproducible pseudo-embeddings (no model needed).

    Seeds a vector from the SHA-256 of each text, then L2-normalises — same text
    always yields the same unit vector, so cosine/RRF/rerank tests are stable.
    """
    out: list[list[float]] = []
    for text in texts:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Expand the 32-byte digest into `dim` floats in [-1, 1].
        vals = [(h[i % len(h)] / 127.5) - 1.0 for i in range(dim)]
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        out.append([v / norm for v in vals])
    return out


def mock_poster(film_id: str, title: str) -> str:
    """Self-contained title-card poster (SVG data URI), hue seeded from film_id.
    Delegates to the shared production helper so demo + real posterless films
    render identically."""
    return title_card_data_uri(title, seed_key=film_id)


def seed_mock_db(conn) -> None:
    """Insert MOCK_TAGS + MOCK_FILMS (and their film_tags) into a test DB."""
    for tag in MOCK_TAGS:
        conn.execute(
            "INSERT OR IGNORE INTO tags (tag_id, dimension, label_en, label_zh_tw, source, status)"
            " VALUES (?,?,?,?,?,?)",
            (
                tag["tag_id"],
                tag["dimension"],
                tag["label_en"],
                tag["label_zh_tw"],
                tag["source"],
                tag["status"],
            ),
        )
    for film in MOCK_FILMS:
        conn.execute(
            "INSERT OR IGNORE INTO films (film_id, title_zh, title_en, description,"
            " catchplay_url, poster_url, original_genre) VALUES (?,?,?,?,?,?,?)",
            (
                film["film_id"],
                film["title_zh"],
                film["title_en"],
                film["description"],
                f"https://example.com/video/{film['film_id']}",
                mock_poster(film["film_id"], film["title_zh"]),
                film["original_genre"],
            ),
        )
        for tid in film["tags"]:
            conn.execute(
                "INSERT OR IGNORE INTO film_tags (film_id, tag_id, source, confidence)"
                " VALUES (?,?,?,?)",
                (film["film_id"], tid, "mock", 1.0),
            )
    conn.commit()
