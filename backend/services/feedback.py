"""FeedbackService — consultant-driven re-analyze of a feedback wiki page.

v1 supports only the `reanalyze` op. Ingest / lint deferred.
Backend dispatch + per-provider HTTP plumbing lives in `backend/llm_client.py`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from backend.feedback_store import apply_reanalyze, get_page
from backend.llm_client import get_llm_client, select_model
from backend.models import FeedbackPage, ReanalyzeResponse
from backend.services.base import BaseService

if TYPE_CHECKING:
    from backend.interfaces import LLMClient

REANALYZE_SCHEMA = {
    "type": "object",
    "required": ["frontmatter_updates", "body_section_title", "body_section_md"],
    "properties": {
        "frontmatter_updates": {
            "type": "object",
            "description": (
                "Partial frontmatter merge. Allowed keys: status, resolution_note, "
                "merged_into, confidence. Omit a key to leave it unchanged."
            ),
        },
        "body_section_title": {
            "type": "string",
            "description": "Heading for new body section, e.g. 'Consultant Validation (2026-04-22)'",
        },
        "body_section_md": {
            "type": "string",
            "description": "Markdown body appended under the new heading",
        },
    },
}

SYSTEM_PROMPT = """You are a senior reviewer for a CATCHPLAY+ feedback wiki.

Given a feedback page (frontmatter + markdown body) plus an editor's natural-language
instruction, decide:
  1. What frontmatter fields should change (status, resolution_note, merged_into, confidence)
  2. What new markdown section to append (validation / rebuttal / refinement)

Lifecycle rules:
- `status=open` → default, still needs attention
- `status=done` → problem resolved (e.g. reject_rate dropped, taxonomy fixed)
- `status=dismissed` → editor decided not to act on it
- `status=merged` → superseded by another page; set `merged_into: <page_id>`

If the editor says "這件不做" / "skip this" / "not doing it" → set status=dismissed with a brief resolution_note.
If the editor says "驗證" / "re-check" / validation prompts → usually leave status, append validation section.
If no instruction given, produce a generic re-validation against current evidence.

The `body_section_md` should cite concrete evidence where possible (films, reject rates, tag ids).
Write body content in Traditional Chinese (繁體中文) to match the existing wiki.

Output JSON matching the provided schema exactly. Do not include any extra keys.
"""


class FeedbackService(BaseService):
    def __init__(self, llm_client: LLMClient | None = None):
        # ADR 0021 injection seam — defaults to the process-wide client.
        self._llm = llm_client or get_llm_client()

    @property
    def name(self) -> str:
        return "feedback"

    async def execute(self, input_data: dict) -> dict:
        op = input_data.get("op", "reanalyze")
        if op != "reanalyze":
            raise NotImplementedError(f"feedback op not supported in v1: {op}")
        return await self._reanalyze(input_data)

    async def _reanalyze(self, input_data: dict) -> dict:
        page_id: str = input_data["page_id"]
        prompt: str = input_data.get("prompt", "") or ""

        page = get_page(page_id)
        if page is None:
            raise FileNotFoundError(f"feedback page not found: {page_id}")

        model = select_model()
        user_prompt = self._build_user_prompt(page, prompt)
        raw = self._llm.call_llm(
            SYSTEM_PROMPT,
            user_prompt,
            model=model,
            schema=REANALYZE_SCHEMA,
            timeout=180.0,
        )
        parsed = self._parse_json(raw)

        updated = apply_reanalyze(
            page_id=page_id,
            frontmatter_updates=parsed.get("frontmatter_updates") or {},
            body_section_title=parsed.get("body_section_title") or "",
            body_section_md=parsed.get("body_section_md") or "",
            model_used=model,
        )

        return ReanalyzeResponse(
            page_id=page_id,
            frontmatter_updates=parsed.get("frontmatter_updates") or {},
            body_section_title=parsed.get("body_section_title") or "",
            body_section_md=parsed.get("body_section_md") or "",
            model_used=model,
        ).model_dump() | {"page": updated.model_dump(mode="json")}

    def _build_user_prompt(self, page: FeedbackPage, editor_prompt: str) -> str:
        fm_summary = {
            "page_id": page.page_id,
            "kind": page.kind,
            "title": page.title,
            "status": page.status,
            "consultant_validated": page.consultant_validated,
            "confidence": page.confidence,
            "sources": page.sources,
        }
        editor_block = (
            editor_prompt.strip()
            if editor_prompt.strip()
            else "(no editor instruction — do a generic re-validation against current evidence)"
        )
        return (
            f"PAGE FRONTMATTER:\n{json.dumps(fm_summary, ensure_ascii=False, indent=2)}\n\n"
            f"PAGE BODY (markdown):\n{page.body}\n\n"
            f"---\nEDITOR INSTRUCTION:\n{editor_block}\n\n"
            f"Return JSON with keys: frontmatter_updates, body_section_title, body_section_md."
        )

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif text.startswith("```"):
            text = text.split("```", 1)[1].split("```", 1)[0]
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return data
