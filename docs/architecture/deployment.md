# 部署 — Deployment

← 回 [README](README.md) · 相關:[search-pipeline](search-pipeline.md)(降級行為) · [sequences](sequences.md)(斷路器)

## 部署拓樸

`docker-compose.yml` 起 backend(build `backend/Dockerfile`)、frontend(build `frontend/Dockerfile`)、qdrant(pull `qdrant/qdrant` image)。Ollama 提供 embedding + 本地 LLM。SQLite 為掛載的檔案 DB。規劃:GHCR 預建 image 走 `docker run`(免本地 build);文件 / portfolio 站部署到 GitHub Pages。

```mermaid
flowchart TB
    subgraph Build["build 路徑 (docker compose)"]
        BEB["backend/Dockerfile → backend 容器"]
        FEB["frontend/Dockerfile → frontend 容器"]
    end

    subgraph Run["執行期 (container host / VPS)"]
        FE["frontend 容器<br/>NiceGUI"]
        BE["backend 容器<br/>FastAPI"]
        QD[("qdrant 容器<br/>qdrant/qdrant image :6333")]
        OLL["Ollama<br/>bge-m3 + qwen2.5:1.5b"]
        VOL[("掛載 volume<br/>SQLite film_library.db")]
    end

    LLMX["雲端 LLM<br/>OpenRouter free (可選)"]

    subgraph GHCR["規劃: GHCR 預建 image"]
        IMG["ghcr.io/.../backend·frontend<br/>docker run, 免本地 build"]
    end

    subgraph Pages["GitHub Pages"]
        SITE["文件 / portfolio 站<br/>(本架構文件)"]
    end

    BEB --> BE
    FEB --> FE
    FE -->|/api/*| BE
    BE --> QD
    BE --> OLL
    BE --> VOL
    BE -.可選.-> LLMX
    IMG -.替代 build.-> Run

    classDef sys fill:#f26f21,stroke:#d4570c,color:#000
    classDef store fill:#1f1f1f,stroke:#f26f21,color:#efefef
    classDef ext fill:#2b2b2b,stroke:#888,color:#efefef
    class BE,FE sys
    class QD,VOL store
    class OLL,LLMX,IMG,SITE ext
```

## 啟動行為 — serve healthy first (ADR 0012)

backend `lifespan` 只同步跑必要步驟(`init_db`);所有 heavy warmup —— tag-vector cache(`get_embed_service().warmup_tag_cache`)、BM25 FTS 重建、cross-encoder load、demo chips —— 都丟背景 thread。慢 / 卡住的 warmup 不再阻塞啟動(曾是 502 來源);第一個需要未暖元件的請求 lazy load 它。

## Keyless / runtime-LLM 降級層

系統 keyless-capable:只接本地模型即可跑完整搜尋,雲端 LLM key 非必須。

```mermaid
flowchart TB
    T1["Tier 1 — 全功能<br/>雲端 LLM (OpenRouter free) 健康<br/>query 理解 + auto-tag 走雲端"]
    T2["Tier 2 — 本地 LLM<br/>雲端限流/無 key/斷路器開<br/>→ 本地 Ollama qwen2.5:1.5b 接手<br/>(query 理解 + auto-tag 仍可跑, 較簡略)"]
    T3["Tier 3 — graceful degrade<br/>雲端+本地 LLM 皆掛<br/>→ 搜尋退回 keyword/vector 召回<br/>(對 query 直接做, 無 tag 展開/HyDE)"]
    BASE["底線 — 始終可用<br/>bge-m3 embedding (本地) + Qdrant 向量召回<br/>+ BM25 lexical + 本地 cross-encoder rerank"]

    T1 -->|雲端失敗| T2
    T2 -->|本地也失敗| T3
    T3 --> BASE
    T1 --> BASE
    T2 --> BASE

    classDef ok fill:#f26f21,stroke:#d4570c,color:#000
    classDef base fill:#1f1f1f,stroke:#f26f21,color:#efefef
    class T1,T2,T3 ok
    class BASE base
```

- 搜尋的召回 / 排序(embedding + Qdrant + BM25 + 本地 reranker)**完全不靠雲端 LLM** —— LLM 只負責 query 理解的 tag 展開與 HyDE,缺席時搜尋仍運作,只是少了語意展開的 boost。
- auto-tag 的雲端→本地切換由斷路器處理([sequences](sequences.md))。
