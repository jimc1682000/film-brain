# 資料模型 — Data Model

← 回 [README](README.md) · 相關:[sequences](sequences.md)

schema 權威定義在 `backend/db.py` 的 `SCHEMA`。下圖列 6 張表的關鍵欄位與關係。

```mermaid
erDiagram
    films ||--o{ film_tags : "carries"
    tags ||--o{ film_tags : "labels"
    films ||--o{ award_nominees : "matched_film_id"
    tags ||--o{ award_nominees : "tag_id"
    films ||--o{ tag_reviews : "film_id"
    tags ||--o{ tag_reviews : "tag_id"
    films ||--o{ similar_films : "film_id (source)"
    films ||--o{ similar_films : "similar_film_id"

    films {
        TEXT film_id PK
        TEXT title_zh
        TEXT title_en
        TEXT description
        TEXT description_raw
        TEXT catchplay_url
        TEXT poster_url
        TEXT original_genre
        INTEGER release_year
        TEXT country_codes
        INTEGER tmdb_id
        TEXT tmdb_overview
        TEXT tmdb_genres
        TEXT tmdb_keywords
        REAL tmdb_vote_avg
        TEXT tmdb_cast
        TEXT tmdb_director
        TEXT tmdb_backdrop_url
        TIMESTAMP created_at
    }

    tags {
        TEXT tag_id PK
        TEXT dimension
        TEXT label_en
        TEXT label_zh_tw
        TEXT label_in_id
        TEXT source
        TEXT status
    }

    film_tags {
        TEXT film_id PK_FK
        TEXT tag_id PK_FK
        REAL confidence
        TEXT source
        INTEGER award_year
        TEXT award_result
        TIMESTAMP created_at
    }

    award_nominees {
        INTEGER id PK
        TEXT org_id
        TEXT tag_id
        INTEGER year
        TEXT category
        TEXT film_title_primary
        TEXT film_title_alt
        TEXT person
        TEXT result
        TEXT source_url
        TEXT ceremony_date
        INTEGER tmdb_id
        TEXT tmdb_media_type
        TEXT tmdb_title
        INTEGER tmdb_year
        TEXT matched_film_id FK
        REAL match_score
        TIMESTAMP created_at
    }

    tag_reviews {
        INTEGER id PK
        TEXT film_id
        TEXT tag_id
        TEXT action
        TEXT reviewer
        TIMESTAMP created_at
    }

    similar_films {
        TEXT film_id PK
        TEXT similar_film_id PK
        INTEGER rank
        REAL score
    }
```

## 關係說明

- **`films` 1—\* `film_tags` \*—1 `tags`**:多對多影片↔標籤,join 表 `film_tags` 帶 `confidence` / `source`,複合主鍵 `(film_id, tag_id)`。`source` 區分標籤來源(migrated / LLM / award curation …);獎項回寫的列另帶 `award_year` / `award_result`。
- **`films` 1—\* `award_nominees`**:nominee 經 `matched_film_id`(模糊比對 ≥ `MATCH_THRESHOLD`)關聯到片庫的片。nominee 列獨立存在 — 片不在庫也保留(刪片時 `matched_film_id` 被清為 NULL,nominee 不刪)。`tag_id` 指向 per-ceremony 的獎項 tag。`UNIQUE (tag_id, year, film_title_primary, person)` 做去重。
- **`films` 1—\* `tag_reviews`**:人工逐 tag 審核紀錄(`action` = approved / rejected / modified)。`reject_rate` 統計餵 feedback wiki。
- **`films` 1—\* `similar_films`**:離線預算的相似片(`scripts/05_compute_similar.py`,跑完整 BM25+vector→RRF→CE 管線),`(film_id, similar_film_id)` 複合主鍵 + `rank` / `score`,讓詳情頁變查表、免付請求時 cross-encoder 成本。
- **`films_fts`**(未畫於 ER):FTS5 虛擬表,`bm25_search.rebuild_fts()` 從 `films` 重建,內容經 jieba 斷詞(space-join)讓 unicode61 tokenizer 看得到中文詞。

## SQLite 權威、Qdrant 只存向量

**SQLite 是唯一權威來源(source of truth)** — 所有結構化資料(影片、標籤、獎項、審核、相似片)都在 `data/film_library.db`。

**Qdrant 只存向量 + 一份 tag-id payload 供過濾**:`vector_store.build_film_payload` 把片向量與一組 tag id 寫進 Qdrant,供向量召回與維度過濾用。**展示資料一律回讀 SQLite**(例:poster 取自 SQL,不取 Qdrant payload — 舊 payload 可能帶 `data:` placeholder)。兩者若不一致,以 SQLite 為準;Qdrant 可由 embedding 流程從 SQLite 重建。
