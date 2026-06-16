---
kind: tags
title: 家庭 (family)
status: open
updated_at: 2026-04-22T10:00:00Z
model_used: hand-written
consultant_validated: false
confidence: 0.6
sources: [tag:family, tag:adults]
---

## Issues

`family` tag 有語意歧義：

1. **Theme**（關於家庭關係的片，例如親情/代際衝突）
1. **Audience**（適合闔家觀賞的片）

目前 66 部被標 `family` 混合這兩類，下游搜尋難區分。

## Evidence

- 同時被標 `adults` (115) + `family` (66) 的片存在 — 照理互斥，暗示標籤定義不清
- CATCHPLAY+ 原始分類有「家庭」類，LLM 可能直接映射，未拆語意

## Suggestions

1. 拆成 `family-drama` (theme) vs `family-friendly` (audience-suitability)
1. Prompt 加入反例：「殺手家族片雖主題是家庭，但不適合 family-friendly」
1. 檢查 `dimension-mapping.json` 這兩 tag 現屬哪個 dimension

## Open Questions

- 編輯面是否傾向保留單一 `family` tag，讓前端用 `adults` 交叉過濾？
- 若拆分，既有 66 部需要逐一回顧
