# ADR 0011 — Query LLM 雲端 free tier 優先,本地 Ollama(qwen2.5:1.5b)為 fallback

- 狀態:Accepted
- 日期:2026-06-10
- 延續:[ADR 0002](0002-llm-query-understanding.md)(LLM query understanding)、[ADR 0009](0009-confidence-tiers-and-honest-scoring.md)(degraded 時誠實標示)
- 相關:免費雲端 LLM 全面失效事故(2026-06)

## 背景

query expansion 原本雲端優先(OpenRouter / Gemini free tier)。2026-06 兩邊同時失效:

- **Gemini** `gemini-3.5-flash`:`429` free-tier 日配額耗盡。
- **OpenRouter** `z-ai/glm-4.5-air:free`:slug 被**下架**(404,改成付費);其他免費 slug(qwen3-next / llama-3.3 / deepseek)**共用池也 429**。

→ query expansion 長期 degraded(雖有 [ADR 0009] 的誠實 fallback,但失去 LLM 理解)。

## 決策

**(rev2, 2026-06-10)最終定為「本地 Ollama 直接為主,不接雲端」。** 免費雲端 free tier 穩定度太低(429/404),先打雲端 + retry 只是在 degrade 前白白增加延遲;直接用本地 qwen2.5:1.5b,本地掛就誠實 degraded(`llm_fallback_backend=""`,無 cloud retry-wait)。

> rev1 曾試「雲端 free 優先,本地 fallback」—— 雲端可用時品質好、GPU 吃大 taxonomy prompt 快;雲端 429 時 fall 到本地 qwen2.5:1.5b(慢、品質較低、會幻覺關鍵字,但能跑),取代原本的 degraded 空白。本地與雲端「同時」掛才 degraded。
>
> 註:曾評估「本地優先」,但本地小模型在 CPU 上跑 taxonomy prompt 慢(暖機 ~8s、冷啟 ~86s)且關鍵字會幻覺(諜報→飛鳥),不適合當主力 → 雲端優先、本地保底。

### 本地模型選型(8GB CPU demo VPS,無 GPU)實測

| 模型               | 結果                                                                                                                         |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `gemma4:e2b`       | 檔案 **7.2GB → OOM**(「有效 2.3B」是 PLE 障眼法)。不可用。                                                                   |
| `qwen3:1.7b`       | thinking 模型,CPU 上每查數分鐘。不可用。                                                                                     |
| `gemma3:1b`        | 小 prompt 快(直打 ollama 5.8s),但 query_expand 的 **taxonomy prompt(395 tag)** 讓它 **2180s(36 分)後 ollama 斷線**。不可用。 |
| **`qwen2.5:1.5b`** | 冷啟首查 ~86s(載模型 + 大 prompt eval),**暖機後 ~8s**。繁體 OK。✅ 選它。                                                    |

`qwen2.5:1.5b` 是唯一在這台 CPU box 上能跑完 taxonomy-heavy prompt 的小模型(無 thinking、prompt eval 較快)。`OLLAMA_KEEP_ALIVE=-1` 讓它常駐,首查後維持暖機。

### 兩個關鍵效能修正

1. **拿掉 Ollama `format=schema`**:Ollama 把 JSON schema 編成 GBNF 文法,配 395-tag enum 文法巨大,CPU 上文法約束解碼是分鐘級。改用 prompt 指示 JSON + `strip_json_fence` + taxonomy 驗證。
1. **繁體靠 prompt 強制**:system prompt「所有輸出一律繁體中文、嚴禁簡體」——小模型預設飄簡體,不能靠模型。

### Fallback 鏈

本地 ollama(qwen2.5:1.5b)→ 失敗 → OpenRouter free tier(best-effort,免費池常爆就 degrade)。雲端 free 恢復或加付費額度時,可改 compose `LLM_BACKEND` / fallback 設定切回雲端優先(GPU 吃大 prompt 快)。

## 後果

- demo 不再因免費雲端配額長期 degraded;本地自給。
- 代價:CPU 上首查冷載 ~86s(keep-alive 後暖機 ~8s);8GB box RAM 臨界,模型只能用 ~1GB 級(qwen2.5:1.5b 986MB);品質低於雲端大模型(夠 query expansion 用)。
- 切模型 = 改 the VPS compose overlay `PRIMARY_MODEL` + VPS `ollama pull` + restart ollama + rebuild backend。
