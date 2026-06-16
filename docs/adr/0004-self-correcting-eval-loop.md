# ADR 0004 — 自我矯正評估閉環(三階段)

- 狀態:Accepted
- 日期:2026-05-27
- 延續:[ADR 0002](0002-llm-query-understanding.md)(P5 eval)、[ADR 0003](0003-tuning-config-and-explainability.md)(可調 config)

## 背景

ADR 0003 把搜尋旋鈕集中到 `search-config.json`,但**值全是手設先驗、沒量化驗證**。要的不是靜態測試,而是**系統能自己量、自己調、自己變好**的閉環。

硬限制:自我矯正需要「訊號量」。Demo 無真實流量 → 無點擊 → 純靠用戶回饋的閉環會餓死。**解法:LLM-as-judge 當裁判**,讓系統不靠流量也能自評 + 自調。

## 北極星(閉環)

```
使用訊號(LLM 裁判 / tag accept-reject / 未來點擊)
   → 隱式相關性標籤(累積)
   → eval(算指標) → auto-tune(掃 config 找最佳)
   → 寫回 search-config.json
   → 部署 → 更準 → 更多訊號  ↺
```

可調表面(`search-config.json`)、標籤回饋(`tag_reviews`)、聚合(`feedback-wiki`)都已存在;本 ADR 補上 eval → tune → 收尾 的自動化。

## 三階段設計

### 階段 1 — 靜態 eval + LLM-as-judge

- `data/eval-queries.json`:一組查詢字串(真實情境;可手寫 + LLM 生成補充)。**不需預標答案**。
- `backend/services/eval_judge.py`:`judge(query, film) -> 0|1|2`(不相關/部分/高度相關),走現有 `llm_client`,結果 cache(query+film_id)。
- `scripts/eval_search.py`:對每條 query 跑搜尋 → 對 top-k 結果用 judge 評分 → 算 **nDCG@k**(主指標)+ precision@k。輸出總分 + 每 query 明細 → `docs/reports/eval-*.json`。
- 用途:任何 config 變更前後跑 = regression gate;給出「現在搜尋多準」的數字。

### 階段 2 — 離線 auto-tune(掃參)

- `scripts/tune_search.py`:對 `search-config.json` 的旋鈕(weights / boost_lambda / rrf_k / min_display_score …)做 grid / random search → 每組跑階段 1 eval → 選 nDCG 最高的。
- 產出 **候選 config**(不直接覆蓋),寫 `data/search-config.candidate.json` + 比較報告。
- 把「拍腦袋值」變「數據選值」。

### 階段 3 — 自動閉環 + 收斂條件

- `scripts/autocorrect_loop.py`:`eval → tune → 套用候選 → 重 eval`,**反覆直到 nDCG(LLM 自評)≥ 0.80 或收斂(連續 N 輪無提升)**。
- 訊號來源(可疊):
  - **LLM 裁判**(主,無流量需求)
  - **tag_reviews**(真實 accept/reject;已在收集)
  - 點擊記錄(未來;先留 stub)
- 護欄(避免自我強化偏見 / 失控):
  - propose→apply gate:候選 config 通過「比現行好且差距顯著」才採用,否則保留現行。
  - 旋鈕限幅(weights/lambda 不超出合理範圍)。
  - 收斂或達標即停,記錄每輪指標到報告。

## 成功條件

- **LLM 自評 nDCG@k ≥ 0.80**,且閉環能自動跑到該門檻並停。

## 誠實 caveat(寫進報告,別自欺)

- **自評是自我參照**:指標由 LLM 裁判給,調到 80% 是「討好這個裁判」,**不等於 80% 真實準確度**。需偶爾人工抽樣校準裁判。
- **偏見閉環**:訊號驅動排序會自我強化(熱門越推越熱)→ 靠護欄 + 多樣性檢查緩解。
- **LLM 成本/限流**:eval×tune = 大量 judge call(免費層 429 風險)→ judge 結果必 cache;query set 控在 ~15-20 條。
- 80% 是「相對裁判」門檻,作為**收斂訊號**,非絕對品質保證。

## 階段交付

- [ ] 階段 1:`eval_judge.py` + `eval_search.py` + `eval-queries.json` + 首份 eval 報告(現況 baseline 數字)
- [ ] 階段 2:`tune_search.py` + 候選 config + 比較報告
- [ ] 階段 3:`autocorrect_loop.py` + 護欄 + 跑到 nDCG ≥ 0.80 + 收斂報告

## 替代方案

- 純人工標 golden set:品質高但慢、無法持續;LLM-judge 可規模化、無流量需求 → 選 LLM-judge,人工只做抽樣校準。
- 直接接真實點擊:最真,但 demo 無流量 → 先 LLM-judge bootstrap,流量到再接。
