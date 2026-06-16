# ADR 0017 — QueryPlan 抽取 + 結構化 understanding(行為保留重組)

- 狀態:Accepted
- 日期:2026-06-12
- 延續:[ADR 0002](0002-llm-query-understanding.md)、[ADR 0015](0015-confirm-direction-gate-before-search.md)、[ADR 0016](0016-structured-exclusion-negative-boost.md)

## 背景

`semantic_search` 把「query 理解」與「ranking」兩件事混在同一個 ~150 行函式:parse_query + expand_query + exclude 解析 + requested 加權 + bm25_text 拼接 + embed,緊接著 recall + inject + CE rerank + 加權 boost + band rescale。字串(`req.query` 被當 embed 輸入 / bm25 base / keywords 黏貼處)散落多處,正是 ADR 0016 排除漏洞的溫床。可讀性與「哪裡該插 exclude」都不清楚。

## 決策

**把「理解半段」抽成 `_build_query_plan(req, embed) -> QueryPlan`,ranking 半段的 tuned 數學原地不動。**

- `QueryPlan` dataclass 持有:`requested`(tag→正向權重)、`excluded_tags`、`bm25_text`、`query_vector`、`extra_vectors`、`understanding`、`expansion_degraded`、`require_award`。建構只做理解,**零 recall/rerank**。
- `semantic_search` 變成:cache check → `plan = _build_query_plan(...)` → unpack → understand_only 早退 → **原封不動的 ranking 區段**(recall / strong-inject / confidence tier / CE rerank / 加權 boost+負 penalty / band rescale)。ranking 變數名不變(`requested`/`bm25_text`/…)→ diff 最小。
- **結構化 understanding**:`understanding["tags"] = [{tag_id, label, dim, weight}]`(additive),與既有扁平 `filters` 標籤並存。讓 gate 能以 tag_id 參照,不只 label。`filters`/`keywords`/`confidence`/`degraded`/`excluded` 全保留(前端 + 測試仍讀)。

### 明確「不做」的(範圍邊界)

以下 smell 在 demo 後曾考慮一併重組,**刻意不動**:

- `parse_query`(regex) 與 `expand_query`(LLM) 雙理解器並存;
- stepback 以 `parser_hits == 0` 為閘;
- CE 的位置感知 blend、band rescale。

理由:這些是 ADR 0004 自評閉環針對特定 eval case 調出來的 tuned 數學,**動它們會改 ranking**。而現在 **gemini free quota 已耗盡**,eval 的 judge + query expansion 都退到本地 ollama(非決定性)→ eval 無法當乾淨的行為保留 oracle(同 code 兩跑 nDCG 都會因 LLM 噪音飄動)。沒有可信的品質量測前動 tuned code = 無 net 的 regression 風險。留待 gemini 額度恢復、eval 恢復決定性後再評估。

## 後果

- 行為保留:**192 個測試全綠**(`test_search_display_band` mock 掉 `expand_query` → 確定性斷言精確排序/分數;若抽取改了 ranking 會 fail)。這是行為保留的硬證據。
- eval 量級一致(non-deterministic 噪音內):nDCG@5 0.9115 vs baseline 0.9159、MAP 0.9149 vs 0.9348、MRR 0.9333 vs 0.9444。差異來自 ollama 非決定性 expansion 每跑召回不同片(82 次 judge cache miss),非行為退化。
- 理解邏輯集中一處、字串拼接不再散落 → 後續要動 query 理解(含 exclude)有單一落點。
- ranking 的 tuned 數學完全未動 → 風險侷限在「搬移是否等價」,由測試保證。
