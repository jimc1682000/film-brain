# ADR 0012 — Startup 先健康後背景 warmup(重模型 lazy load,不阻塞啟動)

- 狀態:Accepted
- 日期:2026-06-10
- 延續:[ADR 0011](0011-local-llm-first-query-expansion.md)(本地/雲端 LLM)、[ADR 0006](0006-eval-backend-readiness.md)(backend readiness)
- 相關:demo backend 502 當機事故(2026-06,startup 卡在 cross-encoder 載入)

## 背景

FastAPI `lifespan` 在 `yield`(= 服務開始接流量)**之前**同步跑了一串重 warmup:

1. tag-vector cache 暖機(embed)、2. BM25 FTS 重建、3. **cross-encoder reranker 載入**(可能從 HuggingFace 下載 ~400MB)、4. demo-chip 全鏈預熱(這步原本就在背景 thread)。

事故:容器重建後 CE reranker 從 HF 下載卡住(unauthenticated 限流 / 網路慢)→ `yield` 永遠到不了 → uvicorn 一直「Waiting for application startup」→ **對外 502 超過 15 分鐘**。RAM/ollama 都正常,純粹是 startup 被一個慢/卡的載入步驟擋住。

> 關鍵誤解修正:卡的是 **CE cross-encoder**(HF 模型,backend python 載),不是 ollama 的 bge-m3/qwen(那些早 pull 到本地,embed 實測 0.1s)。CE 是獨立 HF 模型,容器無 HF cache volume 時每次重建可能重抓。

## 決策

**startup 只做最便宜的必要步驟(`init_db`),立即 `yield` 讓服務健康;所有重 warmup 移進背景 thread,lazy 載入。**

```
init_db()                      # 同步,便宜,必要(schema)
threading.Thread(_bg_warmup)   # 背景:tag cache → FTS rebuild → CE load → demo chips
yield                          # 立即:服務 200,不等 warmup
```

任何 warmup 慢 / 失敗 / 卡住都**不再阻塞啟動**;第一個需要某未暖元件的請求自行 lazy 載(或優雅降級)。各 warmup 各自 try/except,互不影響。

## 後果

- backend **5 秒內健康**(原本卡 15+ 分);startup 不再有單點阻塞 → 消除這類 502。
- 代價:warmup 完成前的**第一次未快取搜尋較慢**(冷載本地 LLM fallback ~86s + 冷載 CE),需付一次冷啟成本;之後暖機 + result-cache 快。demo-chip 背景預熱讓 demo 題秒回。
- 後續可優化:背景 warmup 多加一次 `expand_query` 暖機本地 LLM,讓非 demo 首查也不付冷載 LLM 成本;或替 HF cache 掛 volume 避免重建重抓 CE。
