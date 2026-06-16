# ADR 0005 — LLM 呼叫改用 streaming(為了可取消)

- 狀態:Accepted
- 日期:2026-05-27
- 影響:`backend/llm_client.py::_call_openrouter`(OpenAI-相容後端 — 線上 glm + 本地 Qwen3 eval 裁判)

## 背景

跑自評閉環(ADR 0004)時,中途 `kill` loop 後,本地 lemonade/llama.cpp 的單 slot 仍被佔住,新請求回 `Couldn't connect`。原因不是 loop 沒關掉,而是 **client 端取消傳不到 server 端生成**。

上網查證 llama.cpp:

- 它的 HTTP 是 **blocking httplib,沒有「client 斷線」事件**。
- **non-streaming 請求:無法中途取消** —— client 斷線/取消後,server 照把整個(思考模型很長的)生成跑完才釋放 slot。屬已知未解 issue。
- **streaming 請求:可取消** —— 每生一個 token 嘗試寫回 socket,寫失敗(client 斷)即偵測到 → abort、釋放 slot。

我們原本 `_call_openrouter` 走 non-stream → 製造「孤兒生成」卡住單 slot。

## 決策

`_call_openrouter` 改 **`stream: true`**,逐 chunk 讀 SSE delta、累加回完整字串回傳。對呼叫端介面不變(仍回完整 content),但:

- **可取消**:client 斷線 → server 下個 token 偵測到 → abort → slot 釋放。中斷 loop 不再卡住本地單 slot server。
- 適用所有 OpenAI-相容後端(線上 glm + 本地判官)。
- 429 偵測仍在(stream 開始前檢查 status),維持 fallback 行為。

## 後果

- ✅ 中斷/取消請求會真正停止後端生成,單 slot server 不再被孤兒生成卡死。
- ✅ 內容相同(deltas 累加)。
- ⚠️ 解析略複雜(SSE 逐行 + `[DONE]` + delta.content)。
- ⚠️ 仍是「序列 + 長 timeout」的搭配 —— streaming 解決「主動取消」,但接思考模型仍應給足 timeout、勿讓第二 client 並行打單 slot。

## 操作守則(接本地單 slot 思考模型)

1. 呼叫用 streaming(本 ADR)。
1. timeout 給足(思考模型一題可 10-40s)。
1. **嚴格序列**,勿並行打同一單 slot server(會 `Couldn't connect`)。
1. 真要強制清:`lemonade unload` 砍後端,而非只殺 client。
1. lemonade 重載模型要 `unload` → `load`(只 `load` 會留壞後端)。

## 替代方案

- 維持 non-stream + 「別中途 kill」:脆弱,誤操作就卡。
- 靠 server 端 slot abort API:llama.cpp 無通用 abort endpoint。
- → streaming 是官方文件指出唯一可靠的中途取消途徑。

Sources:

- llama.cpp #6421 — Task Cancellation on Client Disconnection
- llama.cpp #9273 — Cannot properly cancel a non-stream completion request
- llama.cpp #4911 — any simple way to ask server stop generating?
