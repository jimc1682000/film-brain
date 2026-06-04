# Film Brain — AI 片庫大腦

**Live site: https://jimc1682000.github.io/film-brain/**

Static portfolio for an AI film-library prototype built at the CATCHPLAY+ Hackathon 2026:
553 fixed genre nodes reshaped into a 14-dimension / 400-tag flexible taxonomy, with
natural-language semantic search that is honest when nothing truly matches and can
explain every result it returns.

把五百多個固定分類改造成 14 維、400 個標籤的彈性體系 — 編輯用一句話找片,
AI 查不到會誠實說,查得到講得出為什麼。

## Pages

| Page | What's inside |
|---|---|
| [總覽](https://jimc1682000.github.io/film-brain/) | How the system works — auto-tagging & query pipelines, trust mechanisms, demo GIFs |
| [搜尋回放](https://jimc1682000.github.io/film-brain/search.html) | Real pipeline outputs for 7 queries, replayed from canned JSON (incl. the honest low-confidence case) |
| [技術決策](https://jimc1682000.github.io/film-brain/decisions.html) | Stack choices with real rationale, collaboration discipline, major mid-project pivots (ADR digest) |
| [協作方式](https://jimc1682000.github.io/film-brain/collab.html) | How one engineer + AI built this — written for non-technical teammates |
| [MJ 事件](https://jimc1682000.github.io/film-brain/mj-case.html) | A false-100% debugging case study, from symptom to calibrated fix |
| [評測迭代](https://jimc1682000.github.io/film-brain/eval.html) | v1–v8 tuning story driven by an LLM-as-judge eval harness (nDCG 0.93 → 0.96) |

## Notes

- Plain HTML/CSS/JS + CDN libs (marked, mermaid, chart.js) — no build chain.
- Search replay data is genuine pipeline output captured offline; the static site has no backend.
- Application source code lives in a private repository; this repo is the public showcase.
- Catalog metadata from public sources (CATCHPLAY+ / TMDB).
