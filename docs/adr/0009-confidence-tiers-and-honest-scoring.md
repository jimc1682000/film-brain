# ADR 0009 — 誠實計分:三段信心帶(cosine gate),CE 只做排序

- 狀態:Accepted
- 日期:2026-06-09
- 延續:[ADR 0003](0003-tuning-config-and-explainability.md)(可調 config / 顯示分數帶)、[ADR 0001](0001-hybrid-search-qmd.md)(CE 精排)
- 相關:Michael Jackson「假滿分」事故(demo `/explainable` → `/honest` 全記錄)

## 背景

搜尋的內部排序分是**相對值** —— RRF 融合分 min-max 正規化、CE 混分,第一名永遠 ≈1.0,
就算整批不相關(MJ 搜出戰爭片標 100%)。ADR 0003 的初版補法:單一 cosine gate(門檻 0.45)
分「confident / low」兩帶,顯示分映進帶內 `[0.55,0.95] / [0.3,0.65]`,並對**當次顯示的 slice**
重新 min-max 攤開。

這版有三個問題:

1. **per-slice min-max → 分數不穩**:同一 (query, 片) 換 top_k(10↔20)顯示 % 會跳(94↔95),因為攤開的母體變了。
1. **兩帶重疊 + 無命中仍偏高**:low 帶頂 65% > confident 帶底 55%,倒掛;無命中第一名顯示 65%,像半命中。
1. **「每個 confident 查詢第一名都 95%」**:報稅、家族秘密都頂 95%,**頂分不反映查詢品質**,失去代表性。

曾考慮「直接用 CE 絕對分映 %」(好命中高、無命中低、差距保留)。**先量了再決定。**

## 量測(45 真實查詢 + 無命中探針,開 expansion,VPS warm backend)

|                                         | 範圍                                 |
| --------------------------------------- | ------------------------------------ |
| CE ce_max(confident 查詢,n=46)          | 0.793 – 1.000                        |
| CE ce_max(無命中,n=2)                   | 0.805 – 0.880                        |
| cosine top(真實查詢)                    | 0.451 – 0.649(p10 0.505、中位 0.577) |
| cosine top(MJ / 量子)                   | 0.368 / 0.39                         |
| cosine top(報稅 / 股票 / 天氣 / Python) | 0.472 / 0.482 / 0.50 / 0.504         |

兩個鐵證:

- **CE 絕對分不可信**:MJ 無片,CE 仍給頂分 **0.88**,跟真命中區間完全重疊 → 拿來當 % 會顯示 88% 假信心。
- **cosine 是唯一能分辨命中/離題的訊號**:重度離題(MJ/量子)< 0.40,乾淨低於所有真實查詢(min 0.451)。但**軟離題(報稅/股票/Python,0.47–0.50)與最弱真實查詢重疊**,沒有一條線分得開 —— 這是 bge-m3 的天花板。

## 決策

1. **CE 只做查詢內排序,不當絕對品質**(數據否決 CE→% 絕對映射)。
1. **用 cosine(原句向量對片庫最佳)分三段信心**,寫在 `search-config.json` 的 `confidence_tiers`:
   - high:cosine ≥ 0.52 → band `[0.72, 0.95]`、橫幅 ✅ 高度相關
   - mid:0.45 ≤ cosine < 0.52 → band `[0.45, 0.68]`、橫幅 ◐ 部分相關
   - low:cosine < 0.45 → band `[0.20, 0.42]`、橫幅 ⚠ 無高度相關·語意聯想
1. **band 天花板 = 誠實訊號**:第一名的 % 反映「片庫到底有沒有這片」(95 / 68 / 42),不再每查詢都 95%。帶內位置 = CE 排序,絕對值不具意義(明說)。
1. **min-max 改對「整個候選池」算**(非當次 slice)→ 同 (query, 片) 跨 top_k 分數穩定。
1. **門檻取安全側**:成本不對稱(無命中顯示 95% >> 真實查詢被標 ◐),故 high 門檻 0.52 保證所有無命中探針(≤0.504)都進不了 high;代價是 ~5 個最弱真實查詢(cosine 0.45–0.52)歸 mid。
1. 三段 band 不重疊(42\<45、68\<72)→ 跨查詢不再倒掛。

## 後果

- 正面:頂分有代表性;無命中誠實(MJ 65%→42%+警示);報稅類軟離題歸 mid 頂 68%(不再假 95%);跨 top_k 穩定;門檻全在 config,模型換掉重新校準即可。
- 代價:cosine 無法分辨軟離題與弱真實查詢 → 少數弱真實查詢被標 ◐(可接受,結果照給);門檻是針對 bge-m3 + 現片庫校準的先驗,換 embedding / 大改片庫需重量分佈。

## 替代方案(已否決)

- **CE 絕對分映 %**:數據顯示 CE 對離題片給高分(MJ 0.88),不可信。
- **cosine 連續映天花板**:平滑但「為什麼 87%」難解釋,弱化 demo 的可解釋賣點。
- **按名次硬攤分數**:真平手的兩部被迫顯示不同 = 假精度,違背誠實原則。

## 實作

- `data/search-config.json` → `confidence_tiers`
- `backend/routers/search.py` → `_confidence_tier()`、`_assemble_response()`(全池 min-max + tier band);`understanding.confidence` 帶 high/mid/low
- `frontend/pages/search.py` → 三段橫幅(✅/◐/⚠)
- 測試:`backend/tests/integration/test_search_display_band.py`
