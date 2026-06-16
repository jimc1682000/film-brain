import json
from pathlib import Path

from backend.config import settings


class TagRegistry:
    """Load and query the dimension-mapping.json tag taxonomy."""

    def __init__(self, path: Path | None = None):
        self.path = path or settings.dimension_mapping_path
        self._data: dict = {}
        self._tags_by_id: dict[str, dict] = {}
        self._tags_by_dimension: dict[str, list[dict]] = {}
        # (locale, label) -> [tag_id, ...] reverse index. Labels are NOT unique
        # across dimensions (e.g. 犯罪 can live in several), so a label maps to a
        # list. Used to resolve user exclusions (chip ✕) back to tag_ids.
        self._ids_by_label: dict[tuple[str, str], list[str]] = {}
        self.load()

    def load(self) -> None:
        with Path(self.path).open(encoding="utf-8") as f:
            self._data = json.load(f)

        self._tags_by_id = {}
        self._tags_by_dimension = {}
        self._ids_by_label = {}

        for dim_name, dim_data in self._data.get("dimensions", {}).items():
            tags = dim_data.get("tags", [])
            self._tags_by_dimension[dim_name] = tags
            for tag in tags:
                tag_id = tag["tag_id"]
                tag["dimension"] = dim_name
                self._tags_by_id[tag_id] = tag
                for locale, label in (tag.get("labels", {}) or {}).items():
                    if label:
                        self._ids_by_label.setdefault((locale, label), []).append(tag_id)

    @property
    def metadata(self) -> dict:
        return self._data.get("metadata", {})

    @property
    def dimensions(self) -> list[str]:
        return list(self._tags_by_dimension.keys())

    @property
    def all_tag_ids(self) -> set[str]:
        return set(self._tags_by_id.keys())

    def get_tag(self, tag_id: str) -> dict | None:
        return self._tags_by_id.get(tag_id)

    def get_tags_by_dimension(self, dimension: str) -> list[dict]:
        return self._tags_by_dimension.get(dimension, [])

    def get_tag_ids_by_label(self, label: str, locale: str = "zh_TW") -> list[str]:
        """Reverse lookup: all tag_ids whose `locale` label equals `label`.

        Returns a list because labels aren't unique across dimensions. Empty if
        the label is unknown (e.g. it was an LLM keyword, not a taxonomy tag)."""
        return list(self._ids_by_label.get((locale, (label or "").strip()), []))

    def get_dimension_summary(self) -> dict[str, int]:
        return {dim: len(tags) for dim, tags in self._tags_by_dimension.items()}

    def validate_tag_ids(self, tag_ids: list[str]) -> tuple[list[str], list[str]]:
        """Return (valid_ids, invalid_ids)."""
        valid = [t for t in tag_ids if t in self._tags_by_id]
        invalid = [t for t in tag_ids if t not in self._tags_by_id]
        return valid, invalid

    def to_prompt_context(self) -> str:
        """Generate a condensed taxonomy string for LLM prompt injection.
        ~2K tokens covering all dimensions and tag IDs."""
        lines = ["AVAILABLE TAGS BY DIMENSION:\n"]
        for dim in self.dimensions:
            tags = self.get_tags_by_dimension(dim)
            [t["tag_id"] for t in tags]
            # Include zh_TW labels for better Chinese query understanding
            tag_strs = []
            for t in tags:
                label = t.get("labels", {}).get("zh_TW", "")
                tag_strs.append(f"{t['tag_id']}({label})" if label else t["tag_id"])
            lines.append(f"{dim} ({len(tags)}): {', '.join(tag_strs)}")
        return "\n".join(lines)

    def to_db_rows(self) -> list[dict]:
        """Convert to list of dicts ready for SQLite insertion."""
        rows = []
        for tag_id, tag in self._tags_by_id.items():
            labels = tag.get("labels", {})
            status = tag.get("status", "active")
            rows.append(
                {
                    "tag_id": tag_id,
                    "dimension": tag["dimension"],
                    "label_en": labels.get("en", tag_id),
                    "label_zh_tw": labels.get("zh_TW", ""),
                    "label_in_id": labels.get("in_ID"),
                    "source": "migrated",
                    "status": status,
                }
            )
        return rows
