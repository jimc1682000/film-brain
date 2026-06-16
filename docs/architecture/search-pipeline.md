# 搜尋管線 — Hybrid Pipeline

← 回 [README](README.md) · 相關:[components](components.md) · ADR [0001](../adr/0001-hybrid-search-qmd.md) / [0002](../adr/0002-llm-query-understanding.md) / [0009](../adr/0009-confidence-tiers-and-honest-scoring.md)

`backend/routers/search.py` 的 `semantic_search` 與其 helper(`_build_query_plan` · `_apply_query_expansion` · `_inject_strong_tag_films` · `_apply_display_scores` · `_apply_weighted_boost` · `_confidence_tier` · `_assemble_response`)組成混合召回 + 重排管線。query 理解只有一條路徑(LLM),手刻 regex parser 已折入 LLM(ADR 0018)。

## 管線 flowchart

```mermaid
flowchart TB
    Q["查詢 query (+ exclude 結構化排除)"]
    CACHE{"heavy cache 命中?<br/>(key: query+旋鈕)"}
    EXP["expand_query (1 次 LLM call, cached)<br/>gated by use_query_expansion<br/>→ 14 維 tag 加權軟訊號<br/>+ HyDE 假想劇情 + step-back 抽象<br/>+ BM25 keywords + award flag"]
    PLAN["_build_query_plan → QueryPlan<br/>requested 加權 tag · excluded_tags<br/>· bm25_text · query_vector · extra_vectors"]
    EMB["embed query + extra<br/>(1 次 bge-m3 call → 切 query / HyDE / step-back 向量)"]
    GATEQ{"understand_only?<br/>(先回理解讓使用者自評)"}
    REC["hybrid_candidates<br/>多向量召回 (query + HyDE) + BM25 lexical 召回"]
    RRF["fusion.rrf_fuse<br/>per-path 權重 + top-rank bonus"]
    INJ["_inject_strong_tag_films<br/>帶 tag 權重 ≥ 門檻的片注入 (排除 excluded)"]
    TIER["_confidence_tier<br/>best 候選對 USER query 向量 cosine → high/mid/low"]
    DISP["_apply_display_scores<br/>use_llm_rerank → CrossEncoder.rerank<br/>否則 RRF min-max"]
    BOOST["_apply_weighted_boost<br/>+Σ(requested tag 權重) · −exclude penalty (gate ✕)<br/>→ 重排"]
    ASM["_assemble_response<br/>slice top_k · 信心帶天花板 cap %"]
    OUT["SearchResponse"]

    Q --> CACHE
    CACHE -->|hit & !understand_only| ASM
    CACHE -->|miss| EXP
    EXP --> PLAN --> EMB --> GATEQ
    GATEQ -->|yes| OUT
    GATEQ -->|no| REC
    EXP -.tag 權重.-> BOOST
    REC --> RRF --> INJ --> TIER --> DISP --> BOOST --> ASM --> OUT

    classDef m fill:#f26f21,stroke:#d4570c,color:#000
    classDef n fill:#1f1f1f,stroke:#f26f21,color:#efefef
    class EXP,RRF,INJ,TIER,DISP,BOOST m
    class Q,PLAN,EMB,REC,ASM,OUT n
```

- **LLM query 理解**(ADR 0002):一次 call,per-query cached,`use_query_expansion` 控。產出 14 維 taxonomy tag(加權軟 boost 訊號)、HyDE 假想劇情文字、step-back 抽象文字(只用於模糊 query)、BM25 keywords。LLM 失敗 → degrade 成對 `req.query` 直接做 keyword/vector 召回(不再有手刻 parser)。
- **多向量召回**:`VectorStore.search_films` 對 query 向量 + HyDE 向量分別召回;`bm25_search` 做字面召回;全部經 `fusion.rrf_fuse`(per-path 權重 + top-rank bonus)融合。
- **強條件注入**:帶 requested tag 且權重 ≥ 門檻的片被注入,讓高訊號 intent(地區 / 得獎)永遠有結果可排;注入片跳過使用者排除方向的片。
- **統一加權 boost**:片得 `scale × Σ(它帶的 requested tag 權重)`;排除方向(gate ✕)每命中一個 excluded tag 扣大 penalty,讓它掉到顯示門檻下 → 全排除的池誠實回空,不 crash。
- **heavy cache**:key 為 query + 旋鈕;只快取乾淨(非空且 LLM 未 degrade)結果。`understand_only` 不寫 cache。
- **相似片**:離線預算(`scripts/05_compute_similar.py`)寫入 `similar_films` 表;未預算的走即時 cosine fallback。

______________________________________________________________________

## sequence — 一次 `POST /api/search`

```mermaid
sequenceDiagram
    participant FE as Frontend (NiceGUI)
    participant R as routers/search.py
    participant QE as query_expand
    participant LLM as LLMClient<br/>(OpenRouter / Ollama)
    participant EM as Embedder (bge-m3)
    participant HY as hybrid_candidates
    participant VS as VectorStore (Qdrant)
    participant BM as bm25_search (FTS5)
    participant FU as fusion (RRF)
    participant RR as Reranker (bce)
    participant DB as SQLite

    FE->>R: POST /api/search {query, top_k, ...}
    R->>R: heavy cache lookup
    alt cache miss
        R->>QE: expand_query(query)
        QE->>LLM: call_llm (cached per query)
        LLM-->>QE: tags + HyDE + step-back + keywords
        QE-->>R: expansion (degrade → keyword/vector)
        R->>EM: embed([query, HyDE, step-back])
        EM-->>R: query_vector + extra_vectors
        R->>HY: hybrid_candidates(query+HyDE 向量, bm25_text)
        HY->>VS: search_films (多向量召回)
        VS-->>HY: 向量候選
        HY->>BM: BM25 lexical 召回
        BM-->>HY: 字面候選
        HY->>FU: rrf_fuse (per-path 權重 + bonus)
        FU-->>HY: 融合候選
        HY-->>R: candidates
        R->>DB: _inject_strong_tag_films (強 tag 注入)
        R->>R: _confidence_tier (cosine→high/mid/low)
        opt use_llm_rerank
            R->>RR: rerank(query, candidates)
            RR-->>R: 重排候選
        end
        R->>R: _apply_weighted_boost (+boost / −exclude)
        R->>R: cache set (若乾淨)
    end
    R->>DB: _assemble_response (poster 取自 SQL)
    R-->>FE: SearchResponse (信心帶 cap %)
```

______________________________________________________________________

## 信心分級 (ADR 0009)

cosine 是**唯一能分辨真命中 / 離題的訊號**;cross-encoder 絕對分**只能排序、不能當真值**(對無關片也可能給高分)。所以信心帶 + 顯示天花板都 key 在「最佳候選對使用者原句向量的 cosine」,**不是** HyDE 向量(那個 by construction 偏高)。

```mermaid
stateDiagram-v2
    [*] --> 計算top_cos
    計算top_cos --> High: top_cos ≥ tiers.high.min_cos
    計算top_cos --> Mid: ≥ tiers.mid.min_cos
    計算top_cos --> Low: 否則
    High --> [*]: 天花板高 (real match)
    Mid --> [*]: 天花板中
    Low --> [*]: 天花板低 + 離題警示
```

- 帶內位置 = cross-encoder 排序(絕對值無意義),對**整個候選池**做 min-max(非顯示切片)→ 同片跨 `top_k` 分數穩定。
- 顯示帶天花板承載誠實訊號:第一名的 % 反映「片庫到底有沒有這片」,不再每查詢都頂分。
