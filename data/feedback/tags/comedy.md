---
kind: tags
title: 喜劇 (comedy)
status: open
updated_at: 2026-04-22T10:00:00Z
model_used: hand-written
consultant_validated: false
confidence: 0.65
sources: [tag:comedy]
---

## Issues

57 部被標 `comedy`，但未細分子類型：

- 黑色喜劇（dark comedy）
- 諷刺（satire）
- 浪漫喜劇（rom-com）
- 輕鬆喜劇（light comedy）

使用者搜「想看放鬆的喜劇」時，黑色喜劇不應該是第一個結果。

## Evidence

- 同時被標 `comedy` + `dark` 的片有多部（未精確統計）— 這正是「黑色喜劇」但現行 tag 無法直接呈現
- 無 rejection 紀錄（`/api/reviews/stats` 尚無資料）

## Suggestions

1. 現階段：在 search router 加入 tone-aware reranking，讓 `comedy + dark` 在「放鬆」query 下降權
1. 中期：擴 taxonomy 加 `dark-comedy`、`romantic-comedy` 子 tag
1. 先 validate：Consultant 評估是否值得為 57 片做子類拆分

## Open Questions

- 子類對搜尋命中率提升多少？先量化再動 taxonomy
