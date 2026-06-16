# Feedback Wiki Schema v1

LLM-readable contract. Pages live in `data/feedback/`. Each page = markdown with YAML frontmatter.

## Page kinds (v1)

v1 ships only `tags`. Other kinds (`dimension`, `pattern`, `systemic`) reserved — do not produce yet.

| kind   | path               | purpose                                                           |
| ------ | ------------------ | ----------------------------------------------------------------- |
| `tags` | `tags/{tag_id}.md` | Per-tag insight: reject rate, disputes, sample films, suggestions |

## Frontmatter (required)

```yaml
---
kind: tags
title: 驚悚 (thriller)
status: open            # open | done | dismissed | merged
updated_at: 2026-04-22T10:00:00Z
model_used: gemini-3.5-flash
consultant_validated: false
confidence: 0.75
sources: [tag:thriller, review:42]
---
```

## Frontmatter (optional)

- `merged_into: tags/other-tag` — only when `status=merged`
- `resolved_at: 2026-04-25T09:00:00Z` — set when status leaves `open`
- `resolution_note: "reject_rate 從 0.7 跌到 0.12，無需再處理"` — short reason string

## Lifecycle

- `open` — default, shown in UI
- `done` — LLM auto-marks when signal resolved (e.g. reject_rate drop)
- `dismissed` — editor tells LLM "不處理" via re-analyze prompt
- `merged` — superseded by another page; frontmatter points there

Transitions are **LLM-driven** via re-analyze. No UI mark-done button.

## Body conventions

Free markdown. Recommended sections (not required):

- `## Issues` — problems observed
- `## Evidence` — sample films, stats references
- `## Suggestions` — proposed actions
- `## Open Questions` — unresolved
- `## Consultant Validation (YYYY-MM-DD)` — appended by reanalyze

## Re-analyze contract

Consultant returns JSON:

```json
{
  "frontmatter_updates": {"status": "dismissed", "resolution_note": "..."},
  "body_section_title": "Consultant Validation (2026-04-22)",
  "body_section_md": "..."
}
```

Backend merges `frontmatter_updates` into existing frontmatter, appends new section to body, atomic-writes file.
