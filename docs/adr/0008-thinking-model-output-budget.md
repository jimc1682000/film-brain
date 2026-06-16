# ADR 0008 — thinking 模型的 output token 預算

- 狀態:Accepted
- 日期:2026-05-28
- 影響:`backend/config.py::openrouter_max_tokens`(16384 → 32768)
- 關聯:接 ADR 0007(SDK + retry 解掉 warmup 連線失敗後,暴露出本問題)

## 背景

ADR 0007 用 openai SDK + 重試解掉了 warmup 的 `Couldn't connect`(病因 A)。但乾淨 full-45 仍有 ~9/24 query 失敗,訊息仍是 `Expecting value: line 1 column 1 (char 0)`。

關鍵分辨:這個 `char 0` **不是**我們新碼會 raise 的 `LLM stream error` / `failed after retries`,代表 SDK **正常回了空字串、沒拋例外**。→ 串流順利結束但**沒有任何 `content` delta**。

真因(病因 B,與 A 不同):

- moe-q4 = Qwen3 思考模型,輸出走 `reasoning_content` 而非 `content`。
- `reasoning: {enabled: false}` 對 lemonade/llama.cpp **無效,關不掉思考**(lemonade #1511,memory 已記)。
- `max_tokens=16384` 時,最硬的幾題**整個預算被 reasoning 吃光**、還沒輪到答案 content → 累加器(只收 `delta.content`)回空 → 下游 `json.loads("")` 噴 `char 0`。

兩個病因先前被混為一談(都顯示 `char 0`):A 是連線被 gateway 包成假 200+error chunk;B 是思考燒爆預算。A 由 0007 解,B 由本 ADR 解。

## 研究(別人給 Qwen3 多大 output)

- Qwen3 官方建議 thinking mode:**32,768** tokens(一般查詢)、**38,912**(數學/競賽等高難度)。
- Qwen3.6 API 單次 **output 硬上限 65,536** tokens。
- 重要區分:**max output ≠ context window**。output 是 context(本機 256k)內的子預算;65536 是「一次最多生成」,256k 是「prompt + output 總長」。output 長度受 **context 上限**管,**不是 VRAM**(96G 吃得下),代價只是最壞情況多花時間。

## 決策

`openrouter_max_tokens` **16384 → 32768**(Qwen3 thinking 一般建議值)。

- 我們任務(expansion / judge)非競賽級,32768 足夠留思考 + 答案空間。
- 仍遠低於 65536 硬上限,留一倍 headroom:若 32768 還有零星空 content(最硬幾題思考超長),可頂到 **65536**。

## 後果

- ✅ thinking 燒爆預算的空 content 失敗應大幅下降。
- ⚠️ 最壞情況單題生成更久(思考更長);受 256k context 上限保護,不會無限。
- ⚠️ 治本仍在「關不掉思考」;本 ADR 是給夠預算讓答案擠得出來,非消除思考。

## 替代方案

- prompt 加 `/no_think`(Qwen3 inline 關思考):最省、與 ollama path 一致,但本次依使用者選擇先走「加大預算」(option 3),`/no_think` 留作後續可選。
- 換非思考模型跑(`moe-uc-q4` config 標 reasoning:false):另起後端、判官品質待驗 → 暫不換。
- 累加器改也收 `reasoning_content`:思考內容不等於結構化答案,JSON 不保證在裡面 → 不可靠,否決。
