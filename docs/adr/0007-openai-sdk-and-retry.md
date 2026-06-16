# ADR 0007 — LLM 呼叫改用 openai SDK + 重試吃掉 warmup,停手動 unload/load

- 狀態:Accepted
- 日期:2026-05-28
- 影響:`backend/llm_client.py::_call_openrouter`、`pyproject.toml`(加 `openai`)
- 取代:ADR 0006 的 `wait_backend_ready` 降為次級保險(見下)

## 背景

ADR 0006 把本地 lemonade/llama.cpp 後端的 `Couldn't connect` 歸到「load 回報成功但 subprocess 還在 warmup」,加了跑前 readiness poll。但那只是繞過,不是別人的做法。

實際拆解成熟 coding agent(Pi,`@earendil-works/pi-ai`)怎麼打**同一個 lemonade gateway**:

- Pi 的 provider `openai-completions` 直接用**官方 `openai` SDK**:`new OpenAI({ baseURL, apiKey, timeout, maxRetries })` → `client.chat.completions.create(params, { signal, timeout, maxRetries })`。streaming / SSE 解析 / 重試 / 逾時 / 取消(AbortController)全委派 SDK,不手刻。
- Pi 在 **pico 上**的 config:`baseUrl: http://127.0.0.1:13305/v1`(lemonade gateway)、`model: moe-q4`、`contextWindow: 262144`。**證明 13305 gateway + 256k 是對的接法**,Pi 跑得好好的。
- 收尾嚴格:`if (!hasFinishReason) throw "Stream ended without finish_reason"`、in-stream error / abort 都 throw,不吞。

兩邊都打 13305,**Pi 活我們死,純 client handling 差異**:

|                           | Pi                                             | 我們(舊)                             |
| ------------------------- | ---------------------------------------------- | ------------------------------------ |
| HTTP client               | 官方 openai SDK                                | 手刻 httpx streaming                 |
| 重試                      | `maxRetries` 退避(連線錯/5xx/error chunk)      | **只 retry 429**                     |
| 模型管理                  | 打 gateway 帶 model id,**lemonade JIT 自動載** | 手動 `unload`/`load` 瞎攪,製造壞狀態 |
| warmup `Couldn't connect` | SDK 重試自動吃                                 | 一發即炸,不重試                      |

關鍵:lemonade gateway 在後端 warmup 時回 **HTTP 200 + 串流 `{"error": "...Couldn't connect..."}` chunk**(非連線層錯誤)。我們舊碼只看 HTTP 429,這個 in-stream error 直接漏接 → 空字串 → 下游 `char 0`。

## 決策

`_call_openrouter` 改用官方 `openai` SDK,兩層韌性:

1. **SDK `max_retries=2`**:傳輸層失敗(connection refused / timeout / 5xx)自動指數退避重試。
1. **自管重試迴圈(4 次,退避 1/2/4/8s)**:catch `APIError`。openai SDK 在串流遇到 `data: {"error": ...}` 會 `raise APIError`(已查證 `_streaming.py:87`),涵蓋 lemonade warmup error chunk → 退避重試 → **自動吃掉 warmup window**。`RateLimitError`(429)則直接轉 `LLMRateLimitError` 走 call_llm 既有 fallback(Gemini)。

行為守則同步改:

- **停手動 `lemonade unload` / `load`**。打 13305 帶 `model` id,讓 **lemonade JIT 自動載/管**。先前整路 churn 是錯的 handle、還會製造壞後端。
- `OPENROUTER_API_BASE` 用 `…:13305/v1`、`OPENROUTER_API_KEY=lemonade`,對齊 pico pi config。
- streaming 保留(ADR 0005 的可取消特性):SDK stream 的 socket 斷 → 單 slot server abort 生成。

## 後果

- ✅ warmup 的 `Couldn't connect` 被重試自動吸收;實測新碼連 5/5 expand 過。
- ✅ in-stream error / 429 / 連線錯都有對應路徑,不再偽裝成 `char 0`。
- ✅ 不需手動管模型 → 無 churn、無自製壞狀態。
- ⚠️ ADR 0006 的 `wait_backend_ready` 降為**次級保險**(重試已是主要機制);保留為「跑前快速 fail / 明確 log」用,no-op 於常駐後端。可日後移除。
- ⚠️ 新增 `openai` 依賴(SDK 也用 httpx,無額外傳遞性負擔)。

## 為何用 chat.completions 而非 Responses API

openai SDK / OpenAI 官方現在主推 **Responses API**(`client.responses.create`)。lemonade **確實有** `POST /api/v1/responses`(也 `/v1/responses`),所以不是「沒有」。但我們仍用 `chat.completions`,因為:

- 我們的呼叫全是**單輪、無狀態、輸出 JSON**(tagging / judge / expansion)。Responses 的賣點(server 端對話狀態 `previous_response_id`、hosted tools、reasoning item 跨輪保留)我們一個都用不到。
- **可攜性**:`chat.completions` 是通用標準,OpenRouter(雲端 fallback)、Gemini path、各 OpenAI 相容 server 都吃;responses 在 llama.cpp 後端的完整度未驗證,且雲端 fallback 不一定支援 → 換了會多出跨協定的 code path,零功能增益。

## 替代方案

- 繞過 gateway 直連 llama-server `8001`:會拿到真 ECONNREFUSED(SDK 可重試),但**放棄 lemonade 的 JIT 載入 / 模型路由**,且非 pico 既定接法 → 否決。
- 維持手刻 httpx + 自己補重試:能動,但重複造輪、abort/timeout 細節易錯 → 用 SDK 對齊業界。
