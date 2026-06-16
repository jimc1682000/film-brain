# ADR 0003 — 可調設定集中化 + 搜尋可解釋性

- 狀態:Accepted
- 日期:2026-05-27
- 延續:[ADR 0001](0001-hybrid-search-qmd.md)(hybrid 召回)、[ADR 0002](0002-llm-query-understanding.md)(LLM query 理解)

## 背景 (為什麼要改)

0001 + 0002 落地後出現兩個問題:

1. **旋鈕散落、寫死**:RRF 權重、recall/pool、門檻、維度軟硬全是各檔的 module 常數,調一個要改 code + 重建。維度只有二分「硬 filter / 軟」,且寫死 `_HARD_FILTER_DIMS`,新增 taxonomy 維度要改程式。
1. **黑盒**:搜尋只回 score,看不出「為什麼這部上榜 / 排這名」,既難對外展示 AI 有在「理解」,也難 debug 為何怪片排高。

## 決策

### A. 全旋鈕集中到 `data/search-config.json`(熱載)

- 單一設定檔涵蓋:`recall` / `pool` / `rrf_k` / `top_bonus` / `weights{vector,hyde,bm25}` / `min_display_score` / `boost_lambda` / 每維 `dimensions`。
- `services/search_config.py` 依檔案 **mtime 熱載** —— 改檔下次搜尋即生效,免重啟(`data/` 是掛載 volume)。檔損壞/缺漏 → 回退 `_DEFAULTS`,不會弄壞搜尋。
- 檔內含繁中 `_help`(以 `_` 開頭的 key 程式略過)說明每個旋鈕調什麼、調高調低效果。

### B. 維度從「二分軟硬」改成「per-dim mode + weight」

- 每維 `{mode: filter | boost | off, weight}`:
  - `filter` = 硬排除(Qdrant must + AND),只給**定義性/事實性**維(region / award / ip / audience)。
  - `boost` = 軟,符合就加 `boost_lambda × weight` 到候選分數、再 re-sort;不符只排後面。
  - `off` = 忽略。
- **判準**:答錯=根本不對 → filter;答錯=只是不夠貼 → boost。
- `dimension_default`(預設 `boost`/低權重):**未列出的維度自動套用** → 新增 taxonomy 維度免改 code。
- 顯示分數 boost 後 clamp ≤ 1.0(boost 用於排序,不該讓 % 破百)。

### C. 搜尋可解釋性(只露出符號層,神經層誠實標示)

- `SearchResponse.understanding`:query 被讀成的硬 filter + keywords + 是否限得獎 → 前端「🔎 系統理解」橫幅。
- `SearchResult.explain`:`sources`(語意 vector / AI 推想 hyde / 字面 bm25,由 `hybrid_candidates` 記錄召回來源)+ `matched_prefs`(命中的偏好 tag)→ 卡片「命中…」chips。
- **不對稱(刻意)**:符號層(filter / BM25 字面 / tag / boost)完整可解釋;神經層(bge-m3 向量 / CE)只給數字 + 「語意相符」,不假裝能說出原因。

## 後果

- ✅ 調校只改一個 json、即時生效;為 ADR 0002 P5 eval 的權重 sweep 鋪好路。
- ✅ 新維度零 code 變更即可參與搜尋(走 default)。
- ✅ Demo 可展示「AI 理解查詢」;debug 可看召回來源 → 回去調權重(同一條 fine-tune 線)。
- ⚠️ boost 在 CE 路徑上是加在 CE 分數後 → 等於對 CE 排序做微調,`boost_lambda` 太大會壓過 CE,需 eval 校準。
- ⚠️ 設定檔現值仍是手設先驗,未經 eval 量化。

## 待辦 / 已知問題

- **P5 eval harness**(query→預期片 + nDCG/recall + 權重 sweep)仍未做 —— 沒它所有權重無法量化驗證。
- **award 硬篩過嚴**:「韓國得獎犯罪片」回 0 筆(region 硬 + award 硬 + 庫內韓國得獎片少 + award 維本身空、得獎資訊散在 content-type/curation)。待議:award 改軟(偏好得獎)或修正承載維度。

## 替代方案

- 旋鈕放 `settings`/env:要重啟才生效,且分散;json 熱載 + 單檔較適合反覆調校。
- 維度維持二分硬軟:無法表達「偏好強弱」,且新增維度要改 code。
- 可解釋性連神經層也硬解:會編造不存在的理由,違背誠實原則。
