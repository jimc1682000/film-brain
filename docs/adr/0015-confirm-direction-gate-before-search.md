# ADR 0015 — 搜尋前的「確認方向」聯想 gate

- 狀態:Accepted
- 日期:2026-06-12
- 延續:[ADR 0002](0002-llm-query-understanding.md)(LLM query understanding)、[ADR 0009](0009-confidence-tiers-and-honest-scoring.md)(誠實計分)、[ADR 0011](0011-local-llm-first-query-expansion.md) / [ADR 0014](0014-openrouter-free-tier-restored-query-expansion.md)(query expansion LLM)

## 背景

原搜尋一次完成:query → `expand_query`(聯想 tag/keyword/HyDE)→ recall + rerank → 結果,理解框「AI 怎麼理解你」是**事後**展示。

評審補充意見:更想看到的是「AI 先幫忙聯想、**先不 search 片子**,讓用戶自評是否符合需求;OK 才往下 search,不行就糾正方向 loop」。重點在把 AI 的聯想變成一道**人可介入的閘門(gate)**,而非結果出來才解釋。

## 決策

在搜尋前插入 **understand-only gate**,重用既有 `expand_query`,不另開 LLM 路徑。

### 流程

```
query → AI 聯想(expand_query) → 停。展示理解(無片單)
                                  ├─ 用戶:方向對 / 略過 → 完整搜尋(現有 flow)
                                  └─ 用戶:方向不對 → 補充 → 重新聯想(loop)
```

### Backend

- `SearchRequest` 加 `understand_only: bool = False`。
- `semantic_search()` 在組好 `understanding` 後、進 recall 前 early-return `results=[]`,跳過 embed/recall/rerank。
- **Cache 隔離**:`_heavy_cache_key` 不含 `understand_only`,故 understand-only request **跳過 heavy-cache 讀取**(否則撞到已暖的完整結果會回片單、破 gate);understand-only 也不寫 cache。

### Frontend(`frontend/pages/search.py`)

- `do_search` 拆成 `understand()`(gate)+ `run_full_search()`(完整搜尋)。主搜尋鈕 / Enter → `enter_gate()`。
- Gate UI:重用 `_render_understanding`(無候選 → 無 confidence tier → banner 自動略過)+ 可移除 chips + 補充框 +「方向對 / 重新聯想 / 略過直接搜」三鈕。
- **chip 與自由文字走同一路徑**:chip 點擊 → 把「不要「{label}」」寫進補充框 → 與打字一樣折進 query 重新聯想。後端零分支、不需 label↔tag_id 對映、不需凍結 re-expansion;移除用自然語言餵 LLM。
- **chip 用 `ui.button` 仿 chip 樣式(非 `ui.chip`)**:Quasar 的 `q-chip` removable 只 emit DOM `remove` 事件(NiceGUI 沒接、bool value 也不翻),真實滑鼠點不到 handler;`ui.button` + 構造式 `on_click`(同 demo chips)才吃得到真人點擊。
- **理解 chips 去重**:`filters` 與 `keywords` 常重疊(喜劇/紓壓/…),原本 concat 會各顯兩次 → 保序去重。

### Cache 整合(gate 不被 cache 跳過 + 防 reloop 灌爆)

gate 把搜尋拆兩段後,reloop 變成「低成本 query 產生器」:每次修正都產生一個獨一無二的 effective query(原句+修正),改變了兩層共用 cache 的威脅模型。三層 cache 各司其職:

| Cache                 | 服務                            | 範圍                       |
| --------------------- | ------------------------------- | -------------------------- |
| `history.state`       | 回上一頁 / 短期還原結果         | 每人瀏覽器(後端改動不碰它) |
| `_heavy_cache`        | 跨人重複 query 加速 + demo warm | 後端共用                   |
| `query_expand._cache` | LLM 聯想結果重用                | 後端共用                   |

決策:

1. **gate 永不被 cache 跳過**:understand-only 一律繞過 `_heavy_cache` 讀取(`if cached and not req.understand_only`)。所以同句 query 第二人來,仍會看到 gate;cache 只加速「按方向對之後」那段搜尋。已實測(full 暖過後同句走 gate 仍回空)。
1. **兩層共用 cache 改 LRU + pin demo**(`backend/services/pinned_lru.py`):
   - `query_expand._cache`:無界 dict → `PinnedLRU(256)`。reloop 產的冷 query 自然被淘汰,memory 封頂(~0.5MB)。
   - `_heavy_cache`:64 滿停寫 → `PinnedLRU(64)`(LRU 淘汰,不再「滿就停」)。
   - **pin demo**:warmup 跑完每個 chip 後,顯式 `pin_demo_query()` + `pin_query()` 把該 key 標 pinned → 永不被淘汰。pin 容量算在 maxsize 之外(demo + 256/64 non-pinned),且用顯式 pin 而非全域 warming flag,避開 warm 25 分鐘窗口誤 pin 真實使用者 query 的 race。
1. **要不要 cache 非 chip 的使用者 query?要**。LRU 讓壞處自癒:reloop 冷 query hit rate≈0 → 自動被淘汰;真重複的(含偶爾重打)仍享加速。不需特判 reloop。

### 護欄

- **degraded**:`expand_query` 失敗(`_degraded=True`)時沒有可確認的內容 → 略過 gate 直接走關鍵字+向量 fallback,不叫用戶確認垃圾。
- **逃生口**:「略過,直接搜尋」永遠可按 → 聯想不收斂時 demo 不會卡死在 loop。
- **demo chips**(`chips.json`)**跳過 gate**:策展好的 query,一鍵直接出結果,維持 demo 節奏。
- **分享連結 / reload**(`?q=...`):`restore_or_fetch` fallthrough 走完整搜尋,不進 gate。

### 不做 preview 結果於 gate

評審原話是「先不 search 片子」。在 gate 放完整/數量 preview 會把兩階段敘事壓回「舊搜尋加一步」,且每次 chip 編輯觸發 recall+CE(CPU ~7s)會卡。HyDE「推想劇情」已是具體化素材,讓用戶看劇情大綱判斷方向,零搜尋成本。

## 後果

- AI 的聯想從「事後解釋」升級為「事前可導引的決策點」,符合評審要的 human-in-the-loop。
- 多一次 LLM 聯想呼叫(gate),但 understand-only 跳過 recall/rerank,且重用 expansion;reloop 也只是再一次聯想,輕量。
- chip + 自由文字統一成同一條 re-understand,維護面小;代價是 chip 移除靠自然語言提示,LLM 偶爾仍可能重新聯想出相近 tag(可接受,使用者可再修)。
- 一次 `understand_only` request 不寫 heavy-cache;完整搜尋仍照舊快取,不互汙。
