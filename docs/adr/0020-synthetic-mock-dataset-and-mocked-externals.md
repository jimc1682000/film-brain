# ADR 0020 — 合成 mock 資料集 + mock 外部服務(測試 & 解耦)

- 狀態:Accepted
- 日期:2026-06-13
- 相關:[ADR 0004](0004-self-correcting-eval-loop.md)(eval 閉環)、[ADR 0019](0019-config-externalization-public-readiness.md)

## 背景

兩個需求匯流到同一個解:

1. **每個元件測試覆蓋率要 ≥80%**。量出來後,低覆蓋的模組(`embedder` / `vector_store`
   / `hybrid` / `reranker` / `eval_judge` / `tmdb_lookup`)**全是依賴外部服務的**
   —— bge-m3、Qdrant、cross-encoder、LLM、TMDB API —— 沒有真服務就測不到。
1. **開源要與 CATCHPLAY 資料解耦**:不能把爬來的真實片庫 / 海報資料放進 public repo。

## 決策

導入一份 **完全合成、零外部依賴、零 CATCHPLAY 的超小資料集 + mock 外部服務**:

- `backend/tests/fixtures/mock_films.py`:
  - `MOCK_FILMS`(~10 部自編虛構片,含合法 taxonomy tag_id)+ `MOCK_TAGS`。
  - `fake_embed(texts)`:用 SHA-256 種子產**確定性、可重現的單位向量** ——
    同文字永遠同向量,讓 cosine / RRF / rerank 測試穩定,免真模型。
  - `seed_mock_db(conn)`:把上述塞進 test DB。
- 測試 mock 外部服務:embedder 回 `fake_embed`、Qdrant in-memory stub、
  cross-encoder 固定分、`call_llm` 固定 JSON。
- 這份資料同時當 **public repo 的 sample**(README 標明 bring-your-own),
  並支撐 **e2e**(import → tag → embed → search 全跑在合成資料上)。

## 後果

- 依賴外部服務的模組可被**確定性**測到 ≥80%,不需起 Qdrant / 下載模型 / 連 API。
- 系統證明**不綁 CATCHPLAY 資料**:換一份 catalog 即可跑(核心搜尋/標籤本就通用)。
- 路線:先用虛構片驗核心功能,再混入少量公有領域真片測支微末節的邊界。

## 替代方案

- 起真 Qdrant + 下載 bge-m3 跑整合測試:慢、不確定、CI 難跑、仍需資料。
- 只測純函式、外部模組放生:達不到「每元件 ≥80%」。
