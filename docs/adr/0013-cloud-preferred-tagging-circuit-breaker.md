# ADR 0013 — 雲端優先 tagging + circuit-breaker 健康閘門

- 狀態:Accepted
- 日期:2026-06-11
- 延續:[ADR 0011](0011-local-llm-first-query-expansion.md)(本地/雲端 LLM 分工)、[ADR 0012](0012-background-warmup-serve-healthy-first.md)(背景 warmup)
- 相關:demo 環境「重新分析回 0 標籤 / timeout 500」事故

## 背景

ADR 0011 把 runtime LLM 全部收斂到本地 Ollama `qwen2.5:1.5b`(免費雲端 free tier 太不穩)。query expansion 在本地跑得好。但 **auto-tag / 重新分析在 8GB CPU box 上實質失效**:

- 早期症狀:HTTP 200 但回 **0 標籤**。看 raw output 才發現 `qwen2.5:1.5b` 選對標籤、有信心、有繁中理由,只是把 `tag_id` 跟 `dimension` **欄位寫反** → 驗證時 tag_id 無效被濾光。
- 修了 parse(方向無關解析,見下)後,本地 mac 12s 出 4 完整標籤。但**同一 prompt 在 VPS 上 ~150s 且只回 1 個爛標籤**(「科幻」標給歷史傳記《奧本海默》)。
- 根因:auto-tag prompt 帶 ~2.5k-token 的全 taxonomy context,光 prompt-eval 在這台 CPU 就 ~40s,加上 1.5B 在 RAM 臨界(CE+embed+ollama 全常駐)下 swap thrash + 偶發 ramble → timeout(120s)→ 500。這是**硬體天花板**,不是 code bug。跨 ~5 次觀測本地從沒出過 demo 級結果。

## 決策

**tagging(auto-tag / 重新分析)改「雲端優先 + circuit-breaker 健康閘門」,query expansion 維持本地不變。**

1. **per-task backend**:`select_tagging_backend()` 決定 tagging 走哪:雲端 backend(`tagging_cloud_backend`,預設 `gemini`)若**已設定 + 有 key + circuit 未開** → 走雲端;否則走本地 `llm_backend`(ollama)。query expansion 仍直接用 `llm_backend`(頻繁、便宜、不吃配額)。
1. **circuit breaker**(`backend/llm_client.py` `_CloudCircuit`):雲端呼叫一失敗(429 / timeout / 連線)就**開路 `tagging_cloud_cooldown_s`(預設 300s)**,期間直接跳過雲端走本地 —— **沒有 per-request retry 等待**(這正是 ADR 0011 當初放棄雲端的痛點)。cooldown 過後**半開**:下一個 tagging 請求重試雲端,成功就關路、失敗就重新開路。**無背景 poller、不燒配額**。
1. **cloud→local failover**:`call_llm` 既有的 fallback chain(`llm_fallback_backend=ollama` / `llm_fallback_model=qwen2.5:1.5b`)接住雲端 use-time 失敗 → 降級本地;`meta.fallback` 觸發 UI「雲端模型暫不可用,已改用本地模型(結果較簡略)」。`note_tagging_outcome()` 把這結果回饋給 circuit。
1. **bounded local fallback**:本地這條已加 `num_predict=800`(擋 runaway 輸出)+ tagging timeout 180s(prompt-eval headroom),並 parse 方向無關 + dedupe —— 確保「雲端不可用時本地至少不 500、能回幾個標籤」。
1. **無 key 自然降級**:VPS 現為 ollama-only(無 gemini key)→ 閘門直接落到本地;**一旦 fresh key 進 VPS env 就自動升級雲端**,免改 code。
1. **可觀測**:`GET /api/llm-health` 回 `tagging_backend` / `cloud_key_present` / `cloud_available_now` / `circuit`(open + cooldown 剩餘秒)——免讀 log 就看得到當下走哪條。

## 後果

- ✅ tagging 品質回到雲端等級(gemini ~12 標籤)當雲端健康;不健康時誠實降級本地(parse 修好後本地可回幾個正確標籤)。
- ✅ 不再有「雲端掛掉時每個請求都先 retry 等很久」——circuit 開路後 0 等待直接本地。
- ✅ free tier 偶發 429 → 開路 5 分鐘走本地 → 自動半開恢復,無人工介入。
- ⚠️ **前置**:雲端那條要 VPS env 有可用 key;先前外洩的 GEMINI/OPENROUTER key **必須先 rotate** 再放上 VPS(安全債一併清)。在那之前 demo 跑本地降級。
- ⚠️ 本地降級品質仍受限於 1.5B(可接受的 fallback,不是主力)。
- query expansion 行為完全不變。

## 關鍵修正(同日,parse 層)

`_parse_response` 改方向無關:對每個 item,`tag_id` / `dimension` 哪個欄位能在 registry 命中就當真正的 tag_id,dimension/label 一律取自 registry,並 dedupe。小模型欄位寫反也救得回。此修法對雲端輸出無副作用。
