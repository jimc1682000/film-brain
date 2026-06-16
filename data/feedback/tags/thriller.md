---
kind: tags
title: 驚悚 (thriller)
status: open
updated_at: 2026-04-22T10:00:00Z
model_used: hand-written
consultant_validated: false
confidence: 0.7
sources: [tag:thriller, tag:suspenseful, tag:action]
---

## Issues

`thriller` 在目前 395-tag taxonomy 中使用率第 6 (78 films)，與相鄰 tag 邊界模糊：

- 與 `suspenseful` (93 films) 重疊 — 多數 thriller 片同時被標上 suspenseful，反之不然
- 與 `action` (89 films) 混淆 — 追車 / 槍戰片常同時標 action + thriller
- 缺子類：psychological thriller / crime thriller / tech thriller 未區分

## Evidence

- reject-rate 資料尚未累積（`/api/reviews/stats` 回傳 empty），以下屬假設
- 抽樣 10 部 thriller 片中，8 部同時有 `suspenseful`，顯示 tag 概念未切分

## Suggestions

1. 定義 `thriller` = 類型（genre），`suspenseful` = 氛圍（mood）。Prompt 加入區分範例
1. 考慮加子類 tag（`psychological-thriller`, `crime-thriller`）— 需先檢 dimension-mapping.json 是否已有
1. Re-analyze 可以驗證：現行 prompt 是否有此區分？

## Open Questions

- 使用者搜「驚悚片」時 `thriller` 還是 `suspenseful` 命中率高？
- 若子類加進 taxonomy，既有 78 部是否需回溯重標？
