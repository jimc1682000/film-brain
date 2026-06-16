# ADR 0021 — 外部邊界 Protocol 化 + 解耦(分階段)

- 狀態:Accepted(step 1 + step 2 已落地)
- 日期:2026-06-13
- 相關:[ADR 0020](0020-synthetic-mock-dataset-and-mocked-externals.md)(mock 測試策略)

## 背景

系統對外的重活/外部依賴有四個邊界:**embedder**(bge-m3)、**vector store**
(Qdrant)、**reranker**(cross-encoder)、**LLM**(call_llm)。現況:

- embedder 是 class(`EmbedService`),其餘三個是 **module-level functions**
  (`vector_store.search_films`、`reranker.rerank_with_cross_encoder`、
  `llm_client.call_llm`)。
- 測試靠 **monkeypatch 名稱** 去 mock(覆蓋率衝到 97% 就是這樣達成的)。

問題:沒有明確契約,耦合藏在「import 了哪個函式」裡;mock 是「猜名字 patch」,
不是依賴反轉;想換實作(例:換 embedding 模型、換 vector DB)要改 consumer。

## 決策

把四個邊界寫成 **Protocol 契約**(`backend/interfaces.py`):`Embedder` /
`VectorStore` / `Reranker` / `LLMClient`。Protocol 是**結構化 + runtime-checkable**,
不需繼承、不造成 import cycle。

分兩階段,**避免在綠測尾端 big-bang**:

- **Step 1(本 ADR 已落地,零行為改動)**:

  - 定義四個 Protocol。
  - 把 `get_embed_service()` 回傳型別標成 `Embedder`(今天就成立,`EmbedService`
    結構上即滿足)—— 這就是「注入點」的型別契約。
  - 純型別層:不動 runtime、372 測試不受影響。

- **Step 2(已落地,逐邊界做,三個獨立 commit)**:

  - 三個 functional 邊界各包成一個實作對應 Protocol 的 adapter 物件,
    **delegate 回原本的 module function**(保留 fallback / fail-open 語義,
    也讓 source-level patch 仍有效):
    - `reranker.CrossEncoderReranker`(method 名 `rerank`,因與函式名
      `rerank_with_cross_encoder` 不同,wrapper 為必要)+ `get_reranker()`。
    - `vector_store.QdrantVectorStore` + `get_vector_store()`。
    - `llm_client.DefaultLLMClient` + `get_llm_client()`。
  - 經 provider 注入 consumer:
    - service / module function(`hybrid_candidates`、`expand_query`、`judge`)
      改吃 `xxx: Protocol | None = None` 參數,預設 `x or get_x()`。
    - class service(`AutoTagService`、`FeedbackService`)在 `__init__` 注入。
    - FastAPI route(`semantic_search`、`similar_films`)用 `Depends(get_xxx)`,
      測試用 `app.dependency_overrides` 注入 fake。
  - 受影響測試從「monkeypatch 名稱」改成「注入實作 Protocol 的 fake」。
  - **每邊界一個 commit,用現有綠測當回歸護欄**(381 passed、總覆蓋 96.8%、
    每模組 ≥80% gate 不破)。

## 已知 leaky abstraction(後續 follow-up)

`VectorStore.search_films(client, ...)` 仍把 Qdrant `client` 當參數傳進去 ——
store 理論上應自己持有 client。修這個會牽動 indexing / upsert 路徑
(`upsert_film_vector` / `delete_film_vector` / `ensure_collection`),故本次
刻意不擴大 scope,留作後續增量。

## 為何分階段

一次把四個邊界 + 所有 consumer + 全部測試的 monkeypatch 全改 = 高回歸風險。
結構化 Protocol 讓我們可以 boundary-by-boundary 漸進,每步可獨立驗證、可回滾。

## 後果

- 契約變明確、可被型別檢查、可被 fake 明確實作。
- 解耦路徑落地(也呼應「系統可不基於特定資料/服務」的目標):換 vector DB /
  reranker / LLM 只需提供新的 adapter + 改 provider,consumer 不動。
- mock 由 name-patching 升級為 interface 注入。embedder 邊界的 DI 注入點
  (`get_embed_service`)留待有實際 swap 需求時比照辦理。
