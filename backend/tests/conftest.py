import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.db import init_db
from backend.main import app


@pytest.fixture(autouse=True)
def _clear_search_result_cache():
    """Reset the search router's in-process result cache between tests.

    The cache keys on (query, knobs); tests that reuse the same query under
    different mocks would otherwise get a stale cached response.
    """
    from backend.routers import search as _search

    _search._heavy_cache.clear()
    yield
    _search._heavy_cache.clear()


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary SQLite database for testing."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def test_conn(test_db):
    """Get a connection to the test database."""
    conn = sqlite3.connect(str(test_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def sample_film():
    """Sample film data for testing."""
    return {
        "film_id": "test-film-001",
        "title_zh": "測試影片",
        "title_en": "Test Film",
        "description": "這是一部測試用的影片。",
        "catchplay_url": "https://www.catchplay.com/tw/video/test-film-001",
        "poster_url": "https://example.com/poster.jpg",
        "original_genre": "劇情",
    }


@pytest.fixture
def sample_tag():
    """Sample tag data for testing."""
    return {
        "tag_id": "comedy",
        "dimension": "genre",
        "label_en": "Comedy",
        "label_zh_tw": "喜劇",
        "source": "migrated",
        "status": "active",
    }
