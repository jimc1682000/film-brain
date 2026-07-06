# ADR 0001 — Hybrid 搜尋 (BM25 + 向量 + RRF + Cross-Encoder),參考 qmd

- 狀態:Accepted
- 日期:2026-05-27
- 參考:[tobi/qmd](https://github.com/tobi/qmd)（README:typed sub-queries → 並行檢索 → RRF fusion → reranking）

## 背景 (為什麼要改)

語意搜尋與「相似影片」原本只靠 **bge-m3 向量 cosine**,實測有三個問題:

1. **分數擠在一起**:整個片庫的 cosine 落在 0.55–0.65 窄帶,similar 影片的 % 幾乎不動,使用者看不出差異。
1. **精確 / 專有名詞查詢弱**:片名、罕見關鍵字在 embedding 上訊號弱,bi-encoder 抓不準。
1. **Cross-Encoder 全量重排太慢**:`bge-reranker-v2-m3` 在 2 vCPU CPU 的 demo VPS 上對 ~40 候選逐 pair 跑要 **42s**;similar 區塊又是同步 render、無 loading → detail 頁卡死 30–44s。

## 決策

採用 qmd 架構的**有價值子集**,不照搬整套:

```
query
  → 向量召回 (Qdrant bge-m3, top 40)   ─┐
  → BM25 召回 (FTS5 + jieba, top 40)   ─┴→ RRF fusion (+ top-rank bonus)
                                           → Cross-Encoder 精排 (optional, gated)
                                           → 顯示
```

- **BM25 / lexical**:SQLite FTS5(`unicode61`)+ **jieba 斷詞**(中文無空格,先斷詞才有真詞 token)。補向量抓不到的字面 / 專有名詞。
- **RRF fusion**:用排名而非原始分數合併向量 + lexical(兩者尺度不同),天然把分數拉開;top-rank bonus 保護精確命中。
- **Cross-Encoder 保留**:準度優先(team 當初用 CE 就是為此)。但因 CPU 慢,降為 **gated**:
  - search:`use_llm_rerank` flag 控制,只重排 fused top-N,配前端 loader。
  - similar:**離線預算** `scripts/05_compute_similar.py` 跑完整管線寫入 `similar_films` 表,API 變成 cheap lookup;未預算的片(剛 import)走快速 cosine fallback。

### 刻意不做(qmd 有但我們略過)

- **LLM query expansion / HyDE**:增加 latency、成本、不穩定性。先靠現有 `query_parser`(硬篩 region/award/setting)+ BM25 補。日後若「語意明顯卻找不到」案例多,再加 cached HyDE。
- **Qwen3-Reranker**:沿用已接好的 `bge-reranker-v2-m3`,不換 model。

## 後果

- ✅ 專有名詞 / 關鍵字召回變好;分數有梯度(RRF + CE 正規化)。
- ✅ similar 頁從 30–44s → sub-second(查表)。
- ✅ CE latency 受控(gated + 預算)。
- ⚠️ 新依賴 `jieba`。
- ⚠️ FTS index 啟動時 rebuild(~百列,cheap);與 films 同步靠 `rebuild_fts()`。
- ⚠️ **similar 有 staleness**:新增片後舊片的 similar 不含新片 → 要重算。三條觸發路徑(canonical = `make recompute-similar`):`scripts/seed_from_file.py --compute-similar` 尾、`library-doctor` skill、`make recompute-similar`。
- ⚠️ jieba 斷詞對 OOV 片名會誤切(`寄生上流`→`寄生/上流`);用 OR-join 子詞召回緩解,**不**把整片名加進 jieba dict(會變單一 token、子詞查不到),只 pin 短 tag label。

## 替代方案

- **trigram tokenizer**:零依賴,但子詞 \<3 字退化、index 肥、誤命中多。選 jieba 換品質。
- **in-memory rank_bm25**:小 corpus 可行,但 FTS5 可持久化、與 DB 同生命週期,較整潔。
- **完全砍 CE 改純 RRF**:快,但犧牲語意精排準度。準度優先 → 保留 CE(gated)。
