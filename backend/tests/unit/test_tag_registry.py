"""Unit tests for backend/tag_registry.py TagRegistry class."""

import json
from pathlib import Path

import pytest

from backend.tag_registry import TagRegistry

# ---------------------------------------------------------------------------
# Shared fixture: registry loaded from the real dimension-mapping.json
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry():
    """TagRegistry loaded from the project's real dimension-mapping.json."""
    mapping_path = Path(__file__).parents[3] / "data" / "dimension-mapping.json"
    return TagRegistry(path=mapping_path)


@pytest.fixture(scope="module")
def mapping_path():
    """Absolute path to the real dimension-mapping.json."""
    return Path(__file__).parents[3] / "data" / "dimension-mapping.json"


# ---------------------------------------------------------------------------
# Fixture: minimal in-memory dimension-mapping for isolated unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_mapping(tmp_path) -> Path:
    """Write a minimal dimension-mapping.json with two dimensions for unit isolation."""
    data = {
        "metadata": {"version": "test"},
        "dimensions": {
            "genre": {
                "tags": [
                    {
                        "tag_id": "comedy",
                        "labels": {"en": "Comedy", "zh_TW": "喜劇", "in_ID": "Komedi"},
                    },
                    {
                        "tag_id": "drama",
                        "labels": {"en": "Drama", "zh_TW": "劇情"},
                    },
                ]
            },
            "emotion": {
                "tags": [
                    {
                        "tag_id": "tearjerker",
                        "labels": {"en": "Tearjerker", "zh_TW": "催淚"},
                        "status": "active",
                    }
                ]
            },
        },
    }
    mapping_file = tmp_path / "dimension-mapping.json"
    mapping_file.write_text(json.dumps(data), encoding="utf-8")
    return mapping_file


@pytest.fixture
def minimal_registry(minimal_mapping) -> TagRegistry:
    return TagRegistry(path=minimal_mapping)


# ---------------------------------------------------------------------------
# Dimension loading
# ---------------------------------------------------------------------------


def test_load_dimensions(registry):
    """The real dimension-mapping.json must expose exactly 14 dimensions."""
    assert len(registry.dimensions) == 14


def test_load_dimensions_known_names(registry):
    """All expected dimension names must be present after loading."""
    expected = {
        "genre",
        "theme",
        "emotion",
        "era",
        "setting",
        "source",
        "region",
        "audience",
        "occasion",
        "narrative",
        "award",
        "ip",
        "content-type",
        "curation",
    }
    assert expected == set(registry.dimensions)


def test_minimal_registry_loads_correct_dimensions(minimal_registry):
    """TagRegistry must expose exactly the dimensions present in the mapping file."""
    assert set(minimal_registry.dimensions) == {"genre", "emotion"}


# ---------------------------------------------------------------------------
# Tag counts
# ---------------------------------------------------------------------------


def test_all_tag_ids_count(registry):
    """The real dimension-mapping.json must expose 400 unique tag IDs.

    Originally 395; Day-4 taxonomy expansion (scripts/expand_taxonomy.py)
    added 5 fine-grained tags (toxic-romance, infidelity-consequence,
    hardcore, family-drama, family-comedy) requested by Vero on Slack
    C0ANU854KSQ for the demo category extension.
    """
    assert len(registry.all_tag_ids) == 400


def test_all_tag_ids_returns_set(registry):
    """all_tag_ids must return a set (no duplicates)."""
    tag_ids = registry.all_tag_ids
    assert isinstance(tag_ids, set)


def test_minimal_registry_tag_count(minimal_registry):
    """Minimal fixture must report exactly 3 tags (2 genre + 1 emotion)."""
    assert len(minimal_registry.all_tag_ids) == 3


# ---------------------------------------------------------------------------
# get_tag
# ---------------------------------------------------------------------------


def test_get_tag_returns_correct_tag(registry):
    """get_tag must return a dict with the correct tag_id and dimension for a known tag."""
    tag = registry.get_tag("comedy")
    assert tag is not None
    assert tag["tag_id"] == "comedy"
    assert tag["dimension"] == "genre"


def test_get_tag_includes_dimension_field(minimal_registry):
    """get_tag must inject the 'dimension' field so callers don't need a second lookup."""
    tag = minimal_registry.get_tag("tearjerker")
    assert tag is not None
    assert tag["dimension"] == "emotion"


def test_get_tag_nonexistent_returns_none(registry):
    """get_tag must return None for an unknown tag_id."""
    assert registry.get_tag("this-tag-does-not-exist") is None


def test_get_tag_nonexistent_returns_none_minimal(minimal_registry):
    """get_tag returns None on a minimal registry for an absent tag_id."""
    assert minimal_registry.get_tag("nonexistent") is None


# ---------------------------------------------------------------------------
# get_tags_by_dimension
# ---------------------------------------------------------------------------


def test_get_tags_by_dimension_returns_correct_tags(minimal_registry):
    """get_tags_by_dimension must return only tags belonging to that dimension."""
    genre_tags = minimal_registry.get_tags_by_dimension("genre")
    assert len(genre_tags) == 2
    tag_ids = {t["tag_id"] for t in genre_tags}
    assert tag_ids == {"comedy", "drama"}


def test_get_tags_by_dimension_unknown_returns_empty(minimal_registry):
    """get_tags_by_dimension must return an empty list for an unknown dimension."""
    assert minimal_registry.get_tags_by_dimension("nonexistent-dim") == []


def test_get_tags_by_dimension_on_real_data(registry):
    """genre dimension on real data must contain at least the core comedy and drama tags."""
    genre_tags = registry.get_tags_by_dimension("genre")
    tag_ids = {t["tag_id"] for t in genre_tags}
    assert "comedy" in tag_ids
    assert "drama" in tag_ids


# ---------------------------------------------------------------------------
# validate_tag_ids
# ---------------------------------------------------------------------------


def test_validate_tag_ids_all_valid(minimal_registry):
    """validate_tag_ids must return all IDs in valid and nothing in invalid when all exist."""
    valid, invalid = minimal_registry.validate_tag_ids(["comedy", "drama"])
    assert set(valid) == {"comedy", "drama"}
    assert invalid == []


def test_validate_tag_ids_all_invalid(minimal_registry):
    """validate_tag_ids must return empty valid list and all IDs in invalid when none exist."""
    valid, invalid = minimal_registry.validate_tag_ids(["foo", "bar"])
    assert valid == []
    assert set(invalid) == {"foo", "bar"}


def test_validate_tag_ids_mixed(minimal_registry):
    """validate_tag_ids must correctly separate known and unknown tag IDs."""
    valid, invalid = minimal_registry.validate_tag_ids(["comedy", "unknown-tag"])
    assert valid == ["comedy"]
    assert invalid == ["unknown-tag"]


def test_validate_tag_ids_empty_input(minimal_registry):
    """validate_tag_ids must handle an empty input list gracefully."""
    valid, invalid = minimal_registry.validate_tag_ids([])
    assert valid == []
    assert invalid == []


def test_validate_tag_ids_returns_tuple(minimal_registry):
    """validate_tag_ids must return a two-element tuple."""
    result = minimal_registry.validate_tag_ids(["comedy"])
    assert isinstance(result, tuple)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# to_prompt_context
# ---------------------------------------------------------------------------


def test_to_prompt_context_contains_dimensions(registry):
    """to_prompt_context output must mention all 14 dimension names."""
    context = registry.to_prompt_context()
    for dim in registry.dimensions:
        assert dim in context, f"Dimension '{dim}' missing from prompt context"


def test_to_prompt_context_starts_with_header(registry):
    """to_prompt_context must begin with the expected header line."""
    context = registry.to_prompt_context()
    assert context.startswith("AVAILABLE TAGS BY DIMENSION:")


def test_to_prompt_context_includes_tag_ids(minimal_registry):
    """to_prompt_context must include tag IDs from the loaded taxonomy."""
    context = minimal_registry.to_prompt_context()
    assert "comedy" in context
    assert "tearjerker" in context


def test_to_prompt_context_includes_zh_tw_labels(minimal_registry):
    """to_prompt_context must embed zh_TW labels in parentheses alongside tag IDs."""
    context = minimal_registry.to_prompt_context()
    # comedy(喜劇) format expected per to_prompt_context implementation
    assert "喜劇" in context


def test_to_prompt_context_returns_string(registry):
    """to_prompt_context must return a non-empty string."""
    context = registry.to_prompt_context()
    assert isinstance(context, str)
    assert len(context) > 0


# ---------------------------------------------------------------------------
# to_db_rows
# ---------------------------------------------------------------------------


def test_to_db_rows_count(registry):
    """to_db_rows must return one dict per tag_id in the registry (395 rows)."""
    rows = registry.to_db_rows()
    assert len(rows) == len(registry.all_tag_ids)


def test_to_db_rows_schema(minimal_registry):
    """Each row from to_db_rows must contain the required database column keys."""
    rows = minimal_registry.to_db_rows()
    required_keys = {
        "tag_id",
        "dimension",
        "label_en",
        "label_zh_tw",
        "label_in_id",
        "source",
        "status",
    }
    for row in rows:
        assert required_keys.issubset(row.keys()), f"Row missing keys: {required_keys - row.keys()}"


def test_to_db_rows_source_is_migrated(minimal_registry):
    """Every db row must have source='migrated'."""
    for row in minimal_registry.to_db_rows():
        assert row["source"] == "migrated"


def test_to_db_rows_status_defaults_active(minimal_registry):
    """Tags without an explicit status field must default to 'active' in db rows."""
    rows = minimal_registry.to_db_rows()
    rows_by_id = {r["tag_id"]: r for r in rows}
    # comedy has no status field in the minimal fixture
    assert rows_by_id["comedy"]["status"] == "active"


def test_to_db_rows_label_en_fallback_to_tag_id(tmp_path):
    """to_db_rows must use tag_id as fallback label_en when 'en' label is absent."""
    data = {
        "metadata": {},
        "dimensions": {
            "genre": {
                "tags": [
                    {
                        "tag_id": "no-english-label",
                        "labels": {"zh_TW": "無英文"},
                    }
                ]
            }
        },
    }
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(json.dumps(data), encoding="utf-8")
    reg = TagRegistry(path=mapping_file)

    rows = reg.to_db_rows()
    assert rows[0]["label_en"] == "no-english-label"


def test_to_db_rows_label_in_id_none_when_absent(minimal_registry):
    """to_db_rows must set label_in_id to None when 'in_ID' key is not in the labels dict."""
    rows = minimal_registry.to_db_rows()
    rows_by_id = {r["tag_id"]: r for r in rows}
    # drama in minimal fixture has no in_ID key
    assert rows_by_id["drama"]["label_in_id"] is None


# ---------------------------------------------------------------------------
# get_dimension_summary
# ---------------------------------------------------------------------------


def test_dimension_summary(registry):
    """get_dimension_summary must return a dict with one entry per dimension."""
    summary = registry.get_dimension_summary()
    assert isinstance(summary, dict)
    assert len(summary) == len(registry.dimensions)


def test_dimension_summary_values_are_counts(registry):
    """get_dimension_summary values must be non-negative integers."""
    summary = registry.get_dimension_summary()
    for dim, count in summary.items():
        assert isinstance(count, int), f"{dim} count is not int"
        assert count >= 0, f"{dim} has negative count"


def test_dimension_summary_matches_tag_list(minimal_registry):
    """get_dimension_summary counts must match the actual get_tags_by_dimension list lengths."""
    summary = minimal_registry.get_dimension_summary()
    for dim in minimal_registry.dimensions:
        expected = len(minimal_registry.get_tags_by_dimension(dim))
        assert summary[dim] == expected, (
            f"Mismatch for '{dim}': summary says {summary[dim]}, list has {expected}"
        )


def test_dimension_summary_genre_count(minimal_registry):
    """genre dimension in minimal fixture must report 2 tags in the summary."""
    summary = minimal_registry.get_dimension_summary()
    assert summary["genre"] == 2


# ---------------------------------------------------------------------------
# metadata property
# ---------------------------------------------------------------------------


def test_metadata_returns_dict(registry):
    """metadata property must return the metadata block from the JSON."""
    meta = registry.metadata
    assert isinstance(meta, dict)
    assert "version" in meta


def test_metadata_version(registry):
    """Real mapping metadata version must be a non-empty string."""
    assert registry.metadata.get("version", "") != ""


# ---------------------------------------------------------------------------
# Reload behaviour
# ---------------------------------------------------------------------------


def test_load_called_in_init(minimal_mapping):
    """TagRegistry must call load() automatically during __init__."""
    reg = TagRegistry(path=minimal_mapping)
    # If load() was not called the internal dicts would be empty
    assert len(reg.all_tag_ids) > 0
    assert len(reg.dimensions) > 0


# ---------------------------------------------------------------------------
# Reverse label → tag_id lookup (used to resolve gate ✕ exclusions)
# ---------------------------------------------------------------------------


def test_get_tag_ids_by_label_roundtrip(registry):
    """A tag's own zh_TW label resolves back to (at least) its tag_id."""
    tid = next(iter(registry.all_tag_ids))
    label = (registry.get_tag(tid).get("labels", {}) or {}).get("zh_TW")
    if not label:
        pytest.skip("first tag has no zh_TW label")
    assert tid in registry.get_tag_ids_by_label(label)


def test_get_tag_ids_by_label_unknown_is_empty(registry):
    assert registry.get_tag_ids_by_label("絕對不存在的標籤_xyz") == []


def test_get_tag_ids_by_label_returns_all_matches(minimal_mapping, tmp_path):
    """Labels aren't unique across dimensions → all matching tag_ids returned."""
    mapping = {
        "metadata": {"version": "t"},
        "dimensions": {
            "genre": {"tags": [{"tag_id": "crime", "labels": {"zh_TW": "犯罪"}}]},
            "theme": {"tags": [{"tag_id": "crime-theme", "labels": {"zh_TW": "犯罪"}}]},
        },
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(mapping), encoding="utf-8")
    reg = TagRegistry(path=p)
    assert set(reg.get_tag_ids_by_label("犯罪")) == {"crime", "crime-theme"}
