# ADR 0018 — 把 regex query parser 併入 LLM 查詢理解(移除關鍵字 floor)

- 狀態:Accepted
- 日期:2026-06-13
- 修訂:[ADR 0002](0002-llm-query-understanding.md)(LLM Query Understanding)—— 0002 設計成「LLM 結構化解析 + 生成式擴展」共用一次 call,並保留手刻 `query_parser` 當 LLM 失敗時的 fallback floor。本 ADR 移除該 parser。
- 相關:[ADR 0016](0016-structured-exclusion-negative-boost.md)(全 soft-boost 模型)

## 背景

ADR 0002 上線後,query 理解實際有兩條並存:

1. `services/query_parser.py` — 手刻雙語關鍵字字典(region/setting/genre 約 150 條映射 + award 旗標),純 substring 比對。
1. `services/query_expand.py` — 一次 LLM call 吐 `tags`(14 維 taxonomy,對 `TagRegistry` 驗證防幻覺)+ hyde + keywords + stepback。

演進後三個問題浮現:

- **重複**:LLM expand 已能把 query 映射到 region/genre/setting/award 的 taxonomy tag,parser 的字典是它的**手刻子集**,而且更脆 —— 新說法 / 錯字 / 未列入的同義詞就漏,且**每新增一個 taxonomy tag 就要同步改字典**。
- **「硬約束」早已不存在**:ADR 0016 之後所有維度都是 soft boost(`dim_mode` 皆 boost、expand 的 `filters` 恆空),parser 產出的 region/genre/setting 也只是 boost,與 expand 的 `boost_tags` 完全重疊。
- parser 真正**獨有**的只剩兩件事:① `_require_award_presence`(泛指「得獎電影」→ 注入整個 award 維度);② `parser_hits` 計數(用來 gate step-back)。

## 決策

移除 `query_parser.py`,把它獨有的兩件事併進 LLM,query 理解收斂成**單一 LLM 路徑**:

1. **`award_presence`** — 加進 expand 的 schema / prompt(第 5 條)/ 輸出。由 LLM 語意判斷「泛指得獎/入圍/獎項電影但沒指明哪個獎」,取代關鍵字比對;router 依此注入 award 維度 boost。
1. **step-back gate** — 原 `parser_hits == 0` 改用 LLM 衍生的 `specific` 訊號(expand 有映射到任何 tag、有 award 意圖、或使用者給了顯式 filter,即視為具體 query,跳過 step-back)。

## 行為變動(誠實)

- **LLM 失敗 / `use_query_expansion` 關閉**:不再有 parser floor → 退成純向量 + BM25(無 tag boost、無 award 注入)。0002 原本是 fallback 回 parser。對 prototype 可接受 —— expansion 預設開、有 query cache + 雲端/本地 fallback,且 BM25 仍是 lexical floor。
- 得獎判定:關鍵字 substring → LLM 語意(更 robust,但依賴 LLM)。
- step-back gating:語意等價(具體 query 不上 step-back)。

## 取捨 / 為何接受

- 砍掉 **219 行手刻字典 + 一個模組**,免去「新 tag 要同步字典」的永久維護。
- 任意 phrasing / 同義 / 錯字都接得住 —— 這正是產品「同一句話不同說法全面展開」的核心價值,本來就該交給模型而非 regex。
- 代價是 degraded path 變陽春;但那是少數路徑,且不阻擋搜尋。

## 量測

- 既有 eval baseline(`rerank=False`):nDCG@5 0.923 / MRR 0.944 / P@5 0.747。合併後應跑同題庫確認無回歸(尤其得獎類 query 的 P@5)。
- 測試:`backend/tests/` 全綠 185 passed / 2 skipped(2026-06-13);新增 2 個 `award_presence` 單元測試,刪除 `test_query_parser.py`。

## 替代方案

- **保留 parser 當 floor**:換來 219 行永久維護 + 兩套理解邏輯要同步,對 prototype 不值。
- **award 也用關鍵字**:同樣脆,且 LLM 本來就在這次 call 裡。
