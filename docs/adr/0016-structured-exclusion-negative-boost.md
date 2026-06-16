# ADR 0016 — 結構化排除(gate ✕)以負 boost 實作

- 狀態:Accepted
- 日期:2026-06-12
- 延續:[ADR 0015](0015-confirm-direction-gate-before-search.md)(confirm-direction gate)、[ADR 0002](0002-llm-query-understanding.md)、ADR 0009(誠實計分)

## 背景

ADR 0015 的 gate 讓使用者點 ✕ 移除不要的方向,當時 chip 移除是把「不要「X」」**文字折進 query** 再重新聯想。實測兩個 bug:

1. **reloop 又聯想出 X**:`expand_query` 的 prompt 從 query 文字抽 tag/keyword,「不要恐怖」裡的「恐怖」literally 在 query → 照抽(prompt 無否定語意)。
1. **search keyword 仍含 X**:`bm25_text = req.query + keywords`,原句整串餵 BM25 → 「恐怖」被 jieba 斷出 → 召回恐怖片。向量召回更糟 —— dense embedding 不懂否定,embed「不要恐怖」仍帶強恐怖語意。

→ 根因:**把否定折進 query 字串對 LLM / BM25 / 向量三層都失效**。

## 決策

**正向修正折進 query(steer LLM/HyDE/向量),否定走結構化 `exclude` list,不進 query。**

具體:

1. `SearchRequest.exclude: list[str]` —— gate ✕ 移除的 label,結構化帶入,**不折進 query**。query 因此保持純正向 → embed / BM25 base 自動乾淨。
1. **反查** `TagRegistry.get_tag_ids_by_label(label, locale="zh_TW")` → 把排除 label 解析成 tag_id(label 跨維不唯一 → **回傳全部 match**)。
1. **負 boost**:沿用既有統一 soft-weight 機制,`bonus = Σ(requested 正向) − exclude_penalty × Σ(片帶的排除 tag 數)`。`exclude_penalty`(search-config knob,預設 10.0)夠大 → 帶排除 tag 的片 `display_score` 壓到 `min_display_score` 以下 → 被 `_assemble_response` 的 floor filter 濾掉、消失。
1. 從 `requested`(正向 boost)、`used_keywords`、`bm25_text` 移除排除項;`understanding` 顯示濾掉、另回 `excluded` 欄位。
1. **strong-inject 跳過帶排除 tag 的片**(注入再懲罰是浪費,且怕它存活)。

### 為何負 boost 而非硬篩

對齊 ADR 0009 的「不硬篩、永不清空」哲學:排除走同一 soft-score channel,只是大負值。若整個候選池都帶排除 tag(例:對「韓國犯罪驚悚片」排除「驚悚」)→ 全壓下 floor → 誠實回空/少量,而非 crash 或硬清空。預設 penalty 偏「權威移除」(符合使用者講「不要X」的字面期待);要軟性降權而非移除,調低 `exclude_penalty` 即可。

### chip 同走 button(非 ui.chip)

gate ✕ chip 用 `ui.button` 仿 chip 樣式 + 構造式 `on_click`(同 demo chips);Quasar `q-chip` 的 remove 只 emit DOM 事件、NiceGUI 不接、真實滑鼠點不到 handler。點擊 → 加進前端 `excludes` set(不再寫文字)→ 重新聯想 / 方向對時以 `exclude` list 送後端。

### gate 顯示重構(順手消重複)

`_render_understanding(u, editable=True)`:gate 階段讓**理解出的 tag/keyword 本身就是可移除 ✕ chips**(去重),不再 understanding box 靜態 badge + 另一排可移除 chips 重複顯示同一 tag。results 頁維持靜態 badge。

## 後果

- 「不要X」對三層(LLM 再聯想 / BM25 / 向量)都生效:X 不進 query → embed/BM25 乾淨;反查 + 負 boost 把帶 X tag 的片移除;LLM 若再聯想出 X 也被 `requested.pop` 擋掉。
- heavy_cache key 納入 `exclude` → 不同排除集不互汙。
- 非排除查詢**零行為改變**(所有 exclude 路徑 `if excluded_*` 守護;空 exclude = no-op)→ eval baseline nDCG@5 0.9159 不受影響;192 測試綠(含排除壓 floor + 反查單元測試)。
- 邊界:排除查詢的主導 tag → 可能回空(誠實),非 bug。
- keyword-only 排除(非 taxonomy tag)→ 反查無 tag_id,只從 keywords/bm25 移除(已因 query 純正向而乾淨),不影響 boost。
