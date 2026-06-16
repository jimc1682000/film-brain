"""Unit tests for backend/vector_store.py (Qdrant client fully mocked)."""

from backend import vector_store as vs
from backend.config import settings
from backend.tests.fixtures.mock_films import fake_embed

# ── Fakes ────────────────────────────────────────────────────────────────────


class _FakeCollections:
    def __init__(self, names):
        self.collections = [type("C", (), {"name": n})() for n in names]


class _FakeHit:
    def __init__(self, payload, score):
        self.payload = payload
        self.score = score


class _FakeQueryResult:
    def __init__(self, points):
        self.points = points


class _FakeRetrieved:
    def __init__(self, vector):
        self.vector = vector


class FakeClient:
    """Records calls so tests can assert behaviour without real Qdrant."""

    def __init__(self, existing_collections=None, query_points_result=None, retrieve_result=None):
        self._existing = existing_collections or []
        self._query_points_result = query_points_result
        self._retrieve_result = retrieve_result
        self.created = []
        self.upserts = []
        self.deletes = []
        self.last_query_filter = "UNSET"

    def get_collections(self):
        return _FakeCollections(self._existing)

    def create_collection(self, collection_name, vectors_config):
        self.created.append((collection_name, vectors_config))

    def upsert(self, collection_name, points):
        self.upserts.append((collection_name, points))

    def delete(self, collection_name, points_selector):
        self.deletes.append((collection_name, points_selector))

    def query_points(self, collection_name, query, query_filter, limit, with_payload):
        self.last_query_filter = query_filter
        return self._query_points_result

    def retrieve(self, collection_name, ids, with_vectors):
        if isinstance(self._retrieve_result, Exception):
            raise self._retrieve_result
        return self._retrieve_result


# ── _point_id_for ────────────────────────────────────────────────────────────


def test_point_id_for_deterministic():
    a = vs._point_id_for("mock-001")
    b = vs._point_id_for("mock-001")
    assert a == b
    assert a != vs._point_id_for("mock-002")
    assert 0 <= a < (1 << 63)


# ── build_film_payload ───────────────────────────────────────────────────────


def test_build_film_payload_dimension_arrays():
    film = {
        "film_id": "mock-010",
        "title_zh": "機械叛變",
        "title_en": "Machine Uprising",
        "poster_url": "https://example.com/p.jpg",
    }
    tags = [
        {"tag_id": "sci-fi", "dimension": "genre"},
        {"tag_id": "action", "dimension": "genre"},
        {"tag_id": "tense", "dimension": "mood"},
    ]
    payload = vs.build_film_payload(film, tags)
    assert payload["film_id"] == "mock-010"
    assert payload["title_en"] == "Machine Uprising"
    assert set(payload["tags"]) == {"sci-fi", "action", "tense"}
    assert set(payload["dim_genre"]) == {"sci-fi", "action"}
    assert payload["dim_mood"] == ["tense"]


def test_build_film_payload_missing_dimension_defaults_unknown():
    film = {"film_id": "x"}
    payload = vs.build_film_payload(film, [{"tag_id": "t1"}])
    assert payload["title_zh"] == ""
    assert payload["title_en"] is None
    assert payload["dim_unknown"] == ["t1"]


# ── ensure_collection ────────────────────────────────────────────────────────


def test_ensure_collection_creates_when_missing():
    client = FakeClient(existing_collections=["other"])
    vs.ensure_collection(client)
    assert len(client.created) == 1
    name, cfg = client.created[0]
    assert name == settings.qdrant_collection
    assert cfg.size == settings.embedding_dim


def test_ensure_collection_skips_when_present():
    client = FakeClient(existing_collections=[settings.qdrant_collection])
    vs.ensure_collection(client)
    assert client.created == []


def test_ensure_collection_default_client(monkeypatch):
    client = FakeClient(existing_collections=[settings.qdrant_collection])
    monkeypatch.setattr(vs, "get_qdrant_client", lambda: client)
    vs.ensure_collection()  # no client arg → uses default
    assert client.created == []


# ── upsert / delete ──────────────────────────────────────────────────────────


def test_upsert_film_vector():
    client = FakeClient()
    vec = fake_embed(["mock-001"])[0]
    vs.upsert_film_vector(client, "mock-001", vec, {"film_id": "mock-001"})
    assert len(client.upserts) == 1
    coll, points = client.upserts[0]
    assert coll == settings.qdrant_collection
    assert points[0].id == vs._point_id_for("mock-001")


def test_delete_film_vector():
    client = FakeClient()
    assert vs.delete_film_vector(client, "mock-001") is True
    assert len(client.deletes) == 1
    assert client.deletes[0][1] == [vs._point_id_for("mock-001")]


# ── search_films ─────────────────────────────────────────────────────────────


def test_search_films_no_filters():
    hits = [
        _FakeHit(
            {
                "film_id": "mock-001",
                "title_zh": "笑園驚魂夜",
                "title_en": "Laugh Manor",
                "poster_url": "p",
                "tags": ["comedy"],
            },
            0.91,
        )
    ]
    client = FakeClient(query_points_result=_FakeQueryResult(hits))
    vec = fake_embed(["query"])[0]
    out = vs.search_films(client, vec, top_k=5)
    assert client.last_query_filter is None
    assert len(out) == 1
    assert out[0]["film_id"] == "mock-001"
    assert out[0]["score"] == 0.91
    assert out[0]["tags"] == ["comedy"]


def test_search_films_with_dimension_filters():
    hit = _FakeHit({}, 0.5)  # empty payload → defaults exercised
    client = FakeClient(query_points_result=_FakeQueryResult([hit]))
    out = vs.search_films(
        client,
        fake_embed(["q"])[0],
        dimension_filters={"genre": ["sci-fi", "action"], "mood": ["tense"]},
    )
    assert client.last_query_filter is not None  # Filter built
    assert out[0]["film_id"] == ""
    assert out[0]["title_zh"] == ""
    assert out[0]["tags"] == []


def test_search_films_empty_filters_no_conditions():
    client = FakeClient(query_points_result=_FakeQueryResult([]))
    vs.search_films(client, fake_embed(["q"])[0], dimension_filters={"genre": []})
    # values empty → must_conditions stays empty → query_filter stays None
    assert client.last_query_filter is None


# ── get_film_vector ──────────────────────────────────────────────────────────


def test_get_film_vector_single():
    vec = fake_embed(["mock-001"])[0]
    client = FakeClient(retrieve_result=[_FakeRetrieved(vec)])
    out = vs.get_film_vector(client, "mock-001")
    assert out is not None
    assert len(out) == len(vec)
    assert out[0] == float(vec[0])


def test_get_film_vector_multivector_returns_none():
    client = FakeClient(retrieve_result=[_FakeRetrieved([[0.1, 0.2], [0.3, 0.4]])])
    assert vs.get_film_vector(client, "x") is None


def test_get_film_vector_empty_results():
    client = FakeClient(retrieve_result=[])
    assert vs.get_film_vector(client, "x") is None


def test_get_film_vector_swallows_exception():
    client = FakeClient(retrieve_result=RuntimeError("qdrant down"))
    assert vs.get_film_vector(client, "x") is None


# ── get_qdrant_client ────────────────────────────────────────────────────────


def test_get_qdrant_client(monkeypatch):
    captured = {}

    def _fake_ctor(host, port):
        captured["host"] = host
        captured["port"] = port
        return "CLIENT"

    monkeypatch.setattr(vs, "QdrantClient", _fake_ctor)
    client = vs.get_qdrant_client()
    assert client == "CLIENT"
    assert captured["host"] == settings.qdrant_host
    assert captured["port"] == settings.qdrant_port


# ── QdrantVectorStore adapter / provider (ADR 0021) ──────────────────────────


def test_adapter_delegates_to_function(monkeypatch):
    """The adapter's search_films() forwards verbatim to the module function."""
    seen = {}

    def _fake(client, query_vector, top_k=10, dimension_filters=None):
        seen["args"] = (client, query_vector, top_k, dimension_filters)
        return ["sentinel"]

    monkeypatch.setattr(vs, "search_films", _fake)
    out = vs.QdrantVectorStore().search_films("C", [0.1], top_k=3, dimension_filters={"d": ["x"]})
    assert out == ["sentinel"]
    assert seen["args"] == ("C", [0.1], 3, {"d": ["x"]})


def test_adapter_satisfies_protocol():
    from backend.interfaces import VectorStore

    assert isinstance(vs.QdrantVectorStore(), VectorStore)


def test_get_vector_store_is_singleton():
    assert vs.get_vector_store() is vs.get_vector_store()
    assert isinstance(vs.get_vector_store(), vs.QdrantVectorStore)
