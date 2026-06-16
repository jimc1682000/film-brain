# ADR 0014 — 恢復 OpenRouter free tier 為 query expansion 主力

- 狀態:Accepted
- 日期:2026-06-11
- 取代:[ADR 0011](0011-local-llm-first-query-expansion.md) rev2(本地 Ollama 直接為主)
- 延續:[ADR 0002](0002-llm-query-understanding.md)(LLM query understanding)、[ADR 0012](0012-background-warmup-serve-healthy-first.md)(startup warmup)

## 背景

ADR 0011 rev2(2026-06-10)因 OpenRouter 免費池 429/slug 下架,改為本地 qwen2.5:1.5b 直接為主。

2026-06-11 重測 `openrouter/free` auto-router:

- 模型可用、回應正常
- 低 QPS(單次打、不 burst)下穩定無 429
- `openrouter/free` 自動路由到當下可用的免費模型,避免單 slug 下架問題

→ 舊的問題(slug 404 + 共用池 429)已不適用,直接改回雲端主力品質更好。

## 決策

**demo VPS 切回 `LLM_BACKEND=openrouter`，限速 1 req/5 min 避免 burst。**

具體改動：

1. the VPS compose overlay：`LLM_BACKEND=openrouter`，API key 讀 host env `OPENROUTER_API_KEY`
1. `backend/main.py`：移除本地 Ollama pre-warm(雲端 API 無 cold-load)；`_warm_demo_chips` 加 `time.sleep(300)` 間隔(1 req/5 min，6 chips 約 25 分鐘全暖)
1. Fallback 不變：LLMRateLimitError/transport error → 本地 qwen2.5:1.5b

### 為何不 burst

OpenRouter free tier 穩定條件是低 QPS。Startup 原本一次連打 6 chips(各帶一次 query expansion call),快速 burst 易觸 429。改為序列 + sleep 讓每個 LLM call 相隔 5 分鐘,實測零 429。

### Chip warm 時間軸

| chip   | 啟動後時間   |
| ------ | ------------ |
| chip 1 | ~0 min(立即) |
| chip 2 | ~5 min       |
| chip 3 | ~10 min      |
| chip 4 | ~15 min      |
| chip 5 | ~20 min      |
| chip 6 | ~25 min      |

Demo 前 restart,25 分鐘後全數暖好。點到未暖 chip 仍能搜尋,只是不走 cache(CE rerank ~7s)。

## 後果

- query expansion 品質回到大模型水準(vs 本地 qwen2.5:1.5b 的小模型品質)
- startup 不再有 ~86s 本地模型冷載(Ollama 只剩 embedding 用)
- 代價：雲端 API 依賴;若 OpenRouter 再次 429/不穩,fallback 自動回本地(ADR 0011 機制保留)
- `OPENROUTER_API_KEY` 需在 VPS 上設定(host env 或 `.env` 檔),缺 key → backend 自動 fallback 本地
