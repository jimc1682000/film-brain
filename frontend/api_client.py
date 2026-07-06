"""HTTP client for communicating with the FastAPI backend."""

import os

import httpx

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


class ApiClient:
    """Sync HTTP client for NiceGUI pages to call backend API."""

    def __init__(self):
        self.base = BACKEND_URL
        self._client = httpx.Client(base_url=self.base, timeout=30)
        # Lazy-loaded tag_id -> zh_TW label, used by film_card chip labels.
        self._tag_labels: dict[str, str] = {}

    def tag_label(self, tag_id: str) -> str:
        """Return localized label for a tag_id; falls back to tag_id if unknown.

        Cached on first call. If backend is down, returns tag_id silently.
        """
        if not self._tag_labels:
            try:
                data = self._client.get("/api/tags/").json()
                tags = data.get("tags", []) if isinstance(data, dict) else data
                self._tag_labels = {t["tag_id"]: t.get("label_zh_tw") or t["tag_id"] for t in tags}
            except Exception:
                return tag_id
        return self._tag_labels.get(tag_id, tag_id)

    def health(self) -> dict:
        return self._client.get("/health").json()

    # --- Films ---
    def list_films(self, search: str = "", limit: int = 50, offset: int = 0) -> dict:
        params: dict[str, str | int] = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        return self._client.get("/api/films/", params=params).json()

    def llm_info(self) -> dict:
        """Active LLM config (primary + fallback model names)."""
        try:
            return self._client.get("/api/llm-info").json()
        except Exception:
            return {}

    def get_film(self, film_id: str) -> dict:
        return self._client.get(f"/api/films/{film_id}").json()

    def delete_film(self, film_id: str) -> dict:
        r = self._client.delete(f"/api/films/{film_id}")
        r.raise_for_status()
        return r.json()

    def recent_tag_activity(self, limit: int = 10) -> dict:
        return self._client.get("/api/films/recent-tag-activity", params={"limit": limit}).json()

    # --- Tags ---
    def list_tags(self, dimension: str = "") -> dict:
        params = {}
        if dimension:
            params["dimension"] = dimension
        return self._client.get("/api/tags/", params=params).json()

    def get_dimensions(self) -> list[dict]:
        return self._client.get("/api/tags/dimensions").json()

    def get_films_by_tag(self, tag_id: str) -> dict:
        return self._client.get(f"/api/tags/{tag_id}/films").json()

    # --- Auto-Tag ---
    def auto_tag(self, film_id: str, locale: str = "zh_TW") -> dict:
        # 240s: a broken-cloud transient costs cloud-cap (~30s) + local fallback
        # (~150s) before the backend returns; normal cloud path is ~14s.
        r = self._client.post(f"/api/auto-tag/{film_id}", params={"locale": locale}, timeout=240)
        r.raise_for_status()
        return r.json()

    def auto_tag_preview(self, payload: dict) -> dict:
        """Ad-hoc tag a new film without DB write. payload = AutoTagPreviewRequest shape."""
        r = self._client.post("/api/auto-tag/preview", json=payload, timeout=240)
        r.raise_for_status()
        return r.json()

    def create_film(self, payload: dict) -> dict:
        """Persist a previewed new film + accepted tags. payload = CreateFilmRequest shape."""
        r = self._client.post("/api/auto-tag/create", json=payload, timeout=180)
        r.raise_for_status()
        return r.json()

    def accept_tags(self, film_id: str, tag_ids: list[str] | None = None) -> dict:
        body = {"tag_ids": tag_ids}
        r = self._client.post(f"/api/auto-tag/{film_id}/accept", json=body)
        r.raise_for_status()
        return r.json()

    # --- Search ---
    def search(
        self,
        query: str,
        top_k: int = 10,
        dimension_filters: dict | None = None,
        understand_only: bool = False,
        exclude: list[str] | None = None,
    ) -> dict:
        body = {
            "query": query,
            "top_k": top_k,
            "understand_only": understand_only,
            "exclude": exclude or [],
        }
        if dimension_filters:
            body["dimension_filters"] = dimension_filters
        # First call loads 568MB cross-encoder → can exceed default 30s.
        r = self._client.post("/api/search/", json=body, timeout=120)
        r.raise_for_status()
        return r.json()

    def similar_films(self, film_id: str, top_k: int = 5) -> dict:
        r = self._client.get(f"/api/search/similar/{film_id}", params={"top_k": top_k})
        r.raise_for_status()
        return r.json()

    # --- Reviews ---
    def submit_review(
        self,
        film_id: str,
        tag_id: str,
        action: str,
        replacement_tag_id: str | None = None,
        replacement_confidence: float | None = None,
    ) -> dict:
        body: dict = {"tag_id": tag_id, "action": action}
        if replacement_tag_id:
            body["replacement_tag_id"] = replacement_tag_id
        if replacement_confidence is not None:
            body["replacement_confidence"] = replacement_confidence
        r = self._client.post(f"/api/films/{film_id}/reviews", json=body)
        r.raise_for_status()
        return r.json()

    def get_reviews(self, film_id: str) -> list[dict]:
        r = self._client.get(f"/api/films/{film_id}/reviews")
        r.raise_for_status()
        return r.json()

    def get_review_stats(self, min_reviews: int = 3) -> list[dict]:
        r = self._client.get("/api/reviews/stats", params={"min_reviews": min_reviews})
        r.raise_for_status()
        return r.json()

    # --- Awards ---
    def list_award_orgs(self) -> list[dict]:
        r = self._client.get("/api/awards/orgs")
        r.raise_for_status()
        return r.json()

    def recent_award_batches(self, limit: int = 12) -> list[dict]:
        r = self._client.get("/api/awards/recent-batches", params={"limit": limit})
        r.raise_for_status()
        return r.json()

    # --- Feedback wiki ---
    def list_feedback_pages(self, status: str | None = None) -> list[dict]:
        params = {}
        if status:
            params["status"] = status
        r = self._client.get("/api/feedback/pages", params=params)
        r.raise_for_status()
        return r.json()

    def get_feedback_page(self, page_id: str) -> dict:
        r = self._client.get(f"/api/feedback/pages/{page_id}")
        r.raise_for_status()
        return r.json()

    def reanalyze_feedback(
        self, page_id: str, prompt: str = "", use_consultant: bool = True
    ) -> dict:
        # Consultant-tier LLM can exceed default 30s.
        r = self._client.post(
            f"/api/feedback/pages/{page_id}/reanalyze",
            json={"prompt": prompt, "use_consultant": use_consultant},
            timeout=300,
        )
        r.raise_for_status()
        return r.json()

    def list_award_nominations(
        self,
        tag_id: str | None = None,
        org_id: str | None = None,
        year: int | None = None,
        film_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: dict[str, str | int] = {"limit": limit}
        if tag_id:
            params["tag_id"] = tag_id
        if org_id:
            params["org_id"] = org_id
        if year is not None:
            params["year"] = year
        if film_id:
            params["film_id"] = film_id
        r = self._client.get("/api/awards/nominees", params=params)
        r.raise_for_status()
        return r.json()


api = ApiClient()
