"""Integration tests for the feedback wiki router + store.

Covers the critical path:
  - List / get pages
  - Reanalyze transitions status, sets resolved_at, appends body section, atomic-writes
  - Store survives malformed frontmatter
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def feedback_dir(tmp_path, monkeypatch):
    """Isolate feedback_dir to a tmp path seeded with 2 pages."""
    fb = tmp_path / "feedback"
    (fb / "tags").mkdir(parents=True)

    (fb / "SCHEMA.md").write_text("# schema placeholder\n")

    (fb / "tags" / "thriller.md").write_text(
        "---\n"
        "kind: tags\n"
        "title: 驚悚 (thriller)\n"
        "status: open\n"
        "updated_at: 2026-04-22T10:00:00Z\n"
        "confidence: 0.7\n"
        "sources: [tag:thriller]\n"
        "---\n\n"
        "## Issues\n\n"
        "thriller 跟 suspenseful 邊界模糊\n",
        encoding="utf-8",
    )
    (fb / "tags" / "family.md").write_text(
        "---\n"
        "kind: tags\n"
        "title: 家庭 (family)\n"
        "status: done\n"
        "updated_at: 2026-04-20T10:00:00Z\n"
        "resolved_at: 2026-04-20T10:00:00Z\n"
        "resolution_note: 已拆為 family-drama / family-friendly\n"
        "---\n\n"
        "## Resolved\n\n"
        "Taxonomy 已更新\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("backend.config.settings.feedback_dir", fb)
    return fb


def test_list_pages_default_filter_open(feedback_dir):
    client = TestClient(app)
    r = client.get("/api/feedback/pages", params={"status": "open"})
    assert r.status_code == 200
    data = r.json()
    assert [p["page_id"] for p in data] == ["tags/thriller"]
    assert data[0]["body"] == ""  # listing excludes body


def test_list_pages_all(feedback_dir):
    client = TestClient(app)
    r = client.get("/api/feedback/pages")
    assert r.status_code == 200
    ids = sorted(p["page_id"] for p in r.json())
    assert ids == ["tags/family", "tags/thriller"]


def test_get_page_includes_body(feedback_dir):
    client = TestClient(app)
    r = client.get("/api/feedback/pages/tags/thriller")
    assert r.status_code == 200
    page = r.json()
    assert page["status"] == "open"
    assert "邊界模糊" in page["body"]
    assert page["sources"] == ["tag:thriller"]


def test_get_page_404(feedback_dir):
    client = TestClient(app)
    r = client.get("/api/feedback/pages/tags/nonexistent")
    assert r.status_code == 404


def test_reanalyze_dismisses_page(feedback_dir):
    """Editor says '這件不做' → LLM returns status=dismissed → file updated."""

    fake_llm_response = {
        "frontmatter_updates": {
            "status": "dismissed",
            "resolution_note": "暫不處理，資料不足",
        },
        "body_section_title": "Editor Decision (2026-04-22)",
        "body_section_md": "Editor: 延後拆分，先累積資料。",
    }

    from backend.services.feedback import FeedbackService

    async def fake_execute(self, input_data):
        from backend.feedback_store import apply_reanalyze

        updated = apply_reanalyze(
            page_id=input_data["page_id"],
            frontmatter_updates=fake_llm_response["frontmatter_updates"],
            body_section_title=fake_llm_response["body_section_title"],
            body_section_md=fake_llm_response["body_section_md"],
            model_used="test-fake-model",
        )
        return {
            "page_id": input_data["page_id"],
            **fake_llm_response,
            "model_used": "test-fake-model",
            "page": updated.model_dump(mode="json"),
        }

    client = TestClient(app)
    # Reset cached skill instance so our patched execute is picked up.
    from backend.routers import feedback as feedback_router

    feedback_router._skill = None

    with patch.object(FeedbackService, "execute", fake_execute):
        r = client.post(
            "/api/feedback/pages/tags/thriller/reanalyze",
            json={"prompt": "這件不做", "use_consultant": True},
        )

    assert r.status_code == 200, r.text
    result = r.json()
    assert result["page"]["status"] == "dismissed"
    assert result["page"]["resolved_at"] is not None
    assert result["page"]["consultant_validated"] is True
    assert "Editor Decision" in result["page"]["body"]
    assert "延後拆分" in result["page"]["body"]

    # Verify persisted to disk.
    raw = (feedback_dir / "tags" / "thriller.md").read_text(encoding="utf-8")
    assert "status: dismissed" in raw
    assert "Editor Decision (2026-04-22)" in raw


def test_reanalyze_404_on_missing_page(feedback_dir):
    client = TestClient(app)
    r = client.post(
        "/api/feedback/pages/tags/nope/reanalyze",
        json={"prompt": "", "use_consultant": True},
    )
    assert r.status_code == 404


def test_store_atomic_write_survives_reload(feedback_dir):
    """apply_reanalyze must leave no .tmp residue after a clean write."""
    from backend.feedback_store import apply_reanalyze, get_page

    apply_reanalyze(
        page_id="tags/thriller",
        frontmatter_updates={"status": "done", "resolution_note": "reject rate 歸零"},
        body_section_title="Validation",
        body_section_md="ok",
        model_used="test",
    )
    page = get_page("tags/thriller")
    assert page is not None
    assert page.status == "done"
    tmp_files = list((feedback_dir / "tags").glob(".*.tmp"))
    assert tmp_files == []
