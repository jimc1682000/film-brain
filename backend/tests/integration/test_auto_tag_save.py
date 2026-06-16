"""Integration tests for /api/auto-tag/{film_id}/save — ensures /save replaces
prior AI suggestions instead of accumulating ghost tags across re-analyze cycles.

Regression case: 乖狗狗 (Good Doggie) ended up with `curation-hkfa-2025-nominee`
that the editor could not see in the UI but persisted across re-analyze runs.
Root cause is upstream (award_nominees fuzzy match) — addressed separately —
but stale AI suggestions must also be cleared on each /save with same source.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.db import (
    get_db,
    get_film_tags,
    init_db,
    insert_film,
    insert_film_tag,
    insert_tag,
)
from backend.main import app
from backend.routers import auto_tag as auto_tag_router


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    monkeypatch.setattr("backend.config.settings.db_path", db_path)
    monkeypatch.setattr("backend.db.settings.db_path", db_path)

    with get_db(db_path) as conn:
        insert_tag(conn, "thriller", "genre", "Thriller", "驚悚")
        insert_tag(conn, "mystery", "genre", "Mystery", "懸疑")
        insert_tag(conn, "family", "theme", "Family", "家庭")
        insert_tag(
            conn,
            "curation-hkfa-2025-nominee",
            "curation-award",
            "HKFA 2025 nominee",
            "香港金像 2025 入圍",
        )
        insert_film(
            conn,
            film_id="guai-doggie",
            title_zh="乖狗狗",
            title_en="Good Doggie",
            description="A doggie film",
        )
        # Pre-existing user-accepted human tag — must survive a re-analyze.
        insert_film_tag(conn, "guai-doggie", "family", confidence=1.0, source="human")
        # Stale award-curation tag from upstream fuzzy match — must also survive
        # (will be fixed by separate validator, not by /save).
        insert_film_tag(
            conn,
            "guai-doggie",
            "curation-hkfa-2025-nominee",
            confidence=1.0,
            source="award-curation",
        )

    fake_registry = type(
        "FakeRegistry",
        (),
        {"all_tag_ids": {"thriller", "mystery", "family", "curation-hkfa-2025-nominee"}},
    )()
    with patch.object(auto_tag_router, "_registry", fake_registry):
        yield TestClient(app), db_path


def _tag_ids_with_sources(db_path, film_id):
    with get_db(db_path) as conn:
        rows = get_film_tags(conn, film_id)
    return {(r["tag_id"], r["source"]) for r in rows}


def test_save_replaces_prior_ai_suggestions(client_db):
    client, db_path = client_db

    # First analyze pass: LLM suggests thriller.
    r1 = client.post(
        "/api/auto-tag/guai-doggie/save",
        json={"suggestions": [{"tag_id": "thriller", "confidence": 0.8}]},
    )
    assert r1.status_code == 200, r1.text
    after_first = _tag_ids_with_sources(db_path, "guai-doggie")
    assert ("thriller", "ai") in after_first

    # Second analyze pass: LLM suggests mystery instead. The stale thriller
    # row from pass 1 must be gone; human + award-curation rows must remain.
    r2 = client.post(
        "/api/auto-tag/guai-doggie/save",
        json={"suggestions": [{"tag_id": "mystery", "confidence": 0.7}]},
    )
    assert r2.status_code == 200, r2.text

    final = _tag_ids_with_sources(db_path, "guai-doggie")
    assert ("mystery", "ai") in final, "new suggestion missing"
    assert ("thriller", "ai") not in final, "stale ai suggestion still present"
    assert ("family", "human") in final, "human tag wrongly cleared"
    assert (
        "curation-hkfa-2025-nominee",
        "award-curation",
    ) in final, "award-curation tag wrongly cleared (must be fixed upstream, not here)"


def test_save_with_non_ai_source_does_not_clear_ai_tags(client_db):
    """A manual save (source=manual) must not touch AI suggestions — only the
    same-source replace is intended (re-analyze cycle)."""
    client, db_path = client_db

    client.post(
        "/api/auto-tag/guai-doggie/save",
        json={"suggestions": [{"tag_id": "thriller", "confidence": 0.8}]},
    )
    # Manual save adds an extra tag with a different source.
    r = client.post(
        "/api/auto-tag/guai-doggie/save",
        json={
            "suggestions": [{"tag_id": "mystery", "confidence": 1.0}],
            "source": "manual",
        },
    )
    assert r.status_code == 200, r.text

    final = _tag_ids_with_sources(db_path, "guai-doggie")
    assert ("thriller", "ai") in final, "ai suggestion wrongly cleared by manual save"
    assert ("mystery", "manual") in final
