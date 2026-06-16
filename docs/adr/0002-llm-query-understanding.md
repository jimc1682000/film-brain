# ADR 0002 — LLM Query Understanding(結構化解析 + 生成式擴展)

- 狀態:Accepted(部分修訂 — 見 [ADR 0018](0018-fold-query-parser-into-llm.md):`query_parser` fallback 已移除,query 理解收斂成單一 LLM 路徑)
- 日期:2026-05-27
- 延續:[ADR 0001](0001-hybrid-search-qmd.md)(hybrid 召回 + RRF + CE)
- 參考:[tobi/qmd](https://github.com/tobi/qmd) 的 query expansion(HyDE / Vec / Lex)

## 背景 (為什麼要改)

ADR 0001 把召回升級成 hybrid(BM25 + 向量 → RRF → CE),但 **query 理解這層還很弱**:

- 目前只有 `services/query_parser.py` —— 手刻關鍵字表,**只覆蓋 3 個維度**(region 12 種、setting 7 種、award 旗標),純字面 substring。
- 漏掉的:其他 11 個 taxonomy 維度(mood / theme / genre nuance / audience…)、同義詞、口語、模糊語意 query(「適合分手後看的」)。
- 向量召回對「語意對但用字不同」「太短 / 太模糊」的 query 也撈不準。

qmd 用一個 fine-tuned 小模型把 query 改寫成三種檢索文字(HyDE 假想文件 / Vec 句 / Lex 關鍵字)再多路 RRF。我們要拿它的「生成式召回擴展」概念,**但不訓練模型**。

## 決策

加兩個**互補**的 query 理解層,**共用一次 LLM call**:

1. **LLM 結構化解析(我們的 domain 層,qmd 沒有)**
   query → 14 維 taxonomy 結構化 filter(硬約束)。升級現有關鍵字 `query_parser`。

   - 收緊召回:把 region/award/setting/genre/mood… 變成可驗證的 tag 條件。

1. **生成式擴展(qmd 的 HyDE / keyword 概念)**
   query → 一段 HyDE 假想劇情 + 一組 BM25 關鍵字(+ 選配 dense 句)。

   - 放寬召回:多生幾種檢索輸入,救「語意對但撈不到」。

兩者方向相反(收緊 vs 放寬),互補。

### 與 qmd 的差異(刻意)

|                        | qmd                          | 我們                                                              |
| ---------------------- | ---------------------------- | ----------------------------------------------------------------- |
| 結構化 taxonomy filter | ❌ 無                        | ✅ 有(domain)                                                     |
| 生成式擴展             | HyDE / Vec / Lex             | HyDE + keywords(+選配句)                                          |
| 模型                   | fine-tuned Qwen3 1.7B + LoRA | **現有 primary LLM(glm-4.5-air via OpenRouter)API prompt,免訓練** |
| 呼叫                   | 本地小模型                   | 1 次 LLM call 一起吐 filter + hyde + keywords                     |

### 流程(接在 ADR 0001 召回之前)

```
query
  → expand_query (1× LLM call, JSON)  ──► {filters, hyde_text, keywords}
        │ filters → 對 tag_registry 驗證(丟幻覺 tag)
        ▼
  多路召回:
    向量(原 query) + 向量(hyde_text) + BM25(原 query + keywords)
    (套 filters 當硬條件)
  → RRF fusion → CE rerank(沿用 0001)
```

## 不另加 LLM / 基礎設施

- 重用 `backend/llm_client.py`(primary = glm-4.5-air/OpenRouter,Gemini 為 fallback;auto-tag/feedback 在用),既有 429 fallback 已接好。
- HyDE 文字 embed 用現成 **bge-m3**,不加 embedding 模型。
- 無 fine-tune、無 LoRA、無新 service 進程。
- 淨增 = 每次(未 cache)搜尋多 1 次 LLM call。

## 風險與緩解

| 風險                                                               | 緩解                                                                                                           |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| **LLM quota**(primary glm OpenRouter free-tier 有限流;search 頻繁) | **query cache 必做**(normalized query → 結果);可選 gate(極短/已結構化 query 跳過);failure 自動 fallback Gemini |
| **taxonomy 幻覺**(LLM 編不存在的 tag)                              | prompt 注入精簡 14 維 tag 清單 + 回來對 `TagRegistry` 驗證,只留真 tag_id                                       |
| latency +1~3s                                                      | cache 命中 = 0;CE 已 ~19s,比例不痛                                                                             |
| LLM 失敗 / 超時                                                    | fallback 回現有關鍵字 `query_parser`,不擋搜尋                                                                  |
| 擴展反而變差                                                       | 上線前用 eval set 量 before/after                                                                              |

## 完整工作項目

- [x] **P1 `services/query_expand.py`** — expand_query(1× call_llm primary glm)+ taxonomy 注入 + TagRegistry 驗證 + in-memory cache + fallback
- [x] **P2 接進 `routers/search.py`** — filters 併入硬條件;多路召回(原 query 向量 + HyDE 向量 + keyword-augmented BM25)→ RRF → 既有 CE
- [x] **P3 設定 / 旗標** — `settings.use_query_expansion`(預設 on)+ `query_expansion_timeout`
- [x] **P4 測試** — unit(幻覺過濾 / cache / fallback / 空 query)+ integration threshold patch
- [ ] **P5 eval** — 小型 query→預期片 set,量擴展前後召回品質(待補)
- [x] **P6 docs** — `architecture.md` §6 更新 + 本 ADR Accepted

> 上線實測(2026-05-27):vague query「適合分手後一個人看的療傷電影」(關鍵字 parser 完全無法處理)經擴展回傳合理療傷暖片;cache 命中 9.5s → 0.9s。

## 替代方案

- **照搬 qmd 訓 LoRA**:本地小模型省 API,但要訓練 + 部署 infra,hackathon 規模不值。用現有 LLM API prompt。
- **只做生成式擴展、不做結構化 filter**:漏掉 domain 硬約束(region/award),維持現有盲點。
- **只做結構化 filter、不做 HyDE**:救不了「語意對但撈不到」的模糊 query。
- → 兩者都做,共用一次 call,成本可控。
