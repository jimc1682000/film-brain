# 關鍵流程 sequence

← 回 [README](README.md) · 相關:[components](components.md) · [search-pipeline](search-pipeline.md)

涵蓋三條寫入 / 處理流程與標籤斷路器。

## (a) 自動標籤一部片 — auto-tag

`backend/services/auto_tag.py` `AutoTagService.execute`。雲端優先(ADR [0013](../adr/0013-cloud-preferred-tagging-circuit-breaker.md)),失敗 fallback 本地。

```mermaid
sequenceDiagram
    participant C as routers/auto_tag.py
    participant AT as AutoTagService
    participant SEL as select_tagging_backend<br/>(斷路器)
    participant TR as TagRegistry
    participant LLM as LLMClient.call_llm
    participant CB as 斷路器 note_outcome

    C->>AT: execute({film, locale})
    AT->>SEL: select_tagging_backend()
    SEL-->>AT: cloud (健康) 或 local
    AT->>TR: taxonomy context (build prompt)
    AT->>LLM: call_llm(system, user, schema)
    Note over LLM: 雲端失敗 → fallback 本地<br/>meta.fallback=True
    LLM-->>AT: 原始 JSON 文字
    AT->>AT: _validate_suggestions<br/>(orientation-agnostic:<br/>registry 認得的欄位即 tag_id, 丟未知)
    AT->>CB: note_tagging_outcome(fell_back)
    Note over CB: fallback → 開斷路器<br/>乾淨 → 關斷路器
    AT-->>C: AutoTagResponse<br/>(TagSuggestion[] + fallback warning)
```

## (b) 餵片 — seed_from_file

`scripts/seed_from_file.py` — 公開的 bring-your-own-films 路徑。讀 `data/films.seed.json`(對 `films.seed.schema.json` 驗證)。CATCHPLAY scraper 是私有 source adapter,輸出同一格式(中性 adapter contract 見 `scripts/adapters/example_adapter.py`)。

```mermaid
sequenceDiagram
    participant S as seed_from_file
    participant V as schema 驗證
    participant PF as parse_film
    participant TR as TagRegistry
    participant DB as SQLite
    participant AT as AutoTagService<br/>(--auto-tag)
    participant EM as EmbedService
    participant VS as vector_store (Qdrant)

    S->>V: 讀 films.seed.json + 驗 schema
    S->>PF: parse_film(raw)
    Note over PF: titles zh→en→first<br/>tags 3 態: 有 / --auto-tag 補 / 無<br/>str 或 {tag_id,confidence}<br/>ISO country → CSV
    S->>DB: init_db
    S->>TR: load taxonomy (to_db_rows → tags 表)
    S->>DB: insert_film + film_tags
    opt --auto-tag (tags 缺)
        S->>AT: 由 LLM 補 tags
        AT-->>S: TagSuggestion[]
        S->>DB: insert film_tags
    end
    S->>EM: build_film_text → embed_single
    EM-->>S: 向量
    S->>VS: upsert_film_vector(build_film_payload)
    opt --compute-similar
        S->>DB: 預算 similar_films
    end
```

## (c) 獎項 — record_nomination

`backend/award_manager.py` `record_nomination`。Wikidata 驗證另由 `scripts/validate_awards_wikidata.py` 跑。

```mermaid
sequenceDiagram
    participant A as routers/awards.py
    participant AM as award_manager
    participant TR as TagRegistry
    participant FM as film_matcher
    participant TM as tmdb_lookup
    participant DB as SQLite

    A->>AM: record_nomination(org, year, category, titles, ...)
    AM->>TR: register_award_tag (per-ceremony tag)
    AM->>FM: find_film_match(primary, alt)
    FM-->>AM: film_id, score
    Note over AM: score ≥ MATCH_THRESHOLD → matched_film_id
    AM->>TM: _resolve_tmdb_for_nominee
    alt 已對到且有 tmdb_id
        TM->>TM: fetch_tmdb_by_id
    else
        TM->>TM: search_tmdb (year + similarity gate)
    end
    TM-->>AM: tmdb metadata (或無)
    AM->>DB: upsert_award_nominee
    opt 有 matched_film_id
        AM->>DB: _apply_curation_tag (curation-award tag → film_tags)
    end
    AM-->>A: 結果 (matched_film_id, score, tmdb)
```

______________________________________________________________________

## 標籤斷路器 — cloud-preferred (ADR 0013)

8GB CPU 機跑不動完整 taxonomy prompt,所以 auto-tag 優先雲端,用時間冷卻型斷路器:失敗後冷卻期內跳過雲端(不做 per-request retry 等待),冷卻過後半開重試,結果決定再開或關閉。無背景輪詢、不燒配額。

```mermaid
stateDiagram-v2
    [*] --> Closed
    Closed: 斷路器關閉 (雲端優先)
    Open: 斷路器開 (冷卻中→跳過雲端, 直接本地)
    HalfOpen: 半開 (冷卻過, 下次 tagging 重試雲端)

    Closed --> Open: 雲端失敗/429/無 key<br/>→ fallback 本地 + record_failure
    Open --> HalfOpen: 冷卻時間到 (cooldown_s)
    HalfOpen --> Closed: 雲端成功 record_success
    HalfOpen --> Open: 雲端再失敗 record_failure
    Closed --> Degrade: 雲端+本地皆掛
    Open --> Degrade: 本地也掛
    Degrade: graceful degrade<br/>(回報失敗, 不 crash)
```

- `cloud_tagging_available()` = 有設 cloud backend + 有 key + 斷路器未開。
- `select_tagging_backend()` 據此回雲端或本地;`note_tagging_outcome(fell_back)` 餵回斷路器。
- keyless / 無雲端時 → 一律走本地,搜尋仍可用([deployment](deployment.md) 降級層)。
