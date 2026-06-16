# ADR 0006 — eval 跑前等後端 ready + streaming error 浮出

- 狀態:Accepted
- 日期:2026-05-28
- 影響:`scripts/eval_search.py::wait_backend_ready`、`backend/llm_client.py::_call_openrouter`

## 背景

接本地 lemonade/llama.cpp 當 eval 判官(ADR 0004)時,full-45 eval 反覆只 scored 24/45,大量呼叫回 `Expecting value: line 1 column 1 (char 0)`(空字串)。連續幾輪都這樣。

一開始誤判成兩個方向,**都錯**:

1. ~~max_tokens 太小,思考模型把 token 用在思考、content 空~~ → 否決:一次性 call 23.8s 吐完整 JSON,budget 夠。

1. ~~`ctx_size: 262144`(256k)KV cache 爆 VRAM,subprocess spawn 即崩~~ → 否決。實際算過:

   ```
   KV/token = 2(K+V) × 48 layers × 4 kv_heads × 128 head_dim × 1.0625(q8_0) ≈ 52 KB
   256k KV  = 262144 × 52 KB ≈ 14 GB
   總計     = 模型 q4 ~22 GB + KV ~14 GB + mmproj/buffer ~4 GB ≈ 40 GB
   ```

   機器有 **96 GB VRAM**,~40 GB 綽綽有餘。256k 單模型 sustained 8/8 實測通過。**ctx/VRAM 從來不是問題。**

真因兩個,都在我們這側可控:

- **後端 warmup window**:`lemonade load` 回報 `Model loaded successfully!` 時,llama-server subprocess(port 8001)還在配置 KV、尚未 ready。腳本一載完立刻硬打 → lemonade gateway 連不上自家 subprocess → 回 streamed error chunk。warmup 實測 ~4s,但腳本零等待。
- **錯誤被吞**:`_call_openrouter` 的 SSE 累加器把 `data: {"error": {...}}`(內容 `CURL error: Couldn't connect to server`)用泛 `except: continue` 跳過 → 回空字串 → 下游 `json.loads("")` 噴 `char 0`,把真因(後端沒 ready)偽裝成 JSON 解析錯。

> 註:`Max Models/Type = 2` 會讓手動 `load` 第二顆模型時兩顆共存搶資源,加重不穩。診斷期間踩過,操作上「全 `unload` → 單 `load`」即可避免,非腳本問題。

## 決策

1. **`_call_openrouter` 把 SSE error chunk 改 raise**(ADR 0005 的 commit 之後補強):偵測 `{"error": ...}` → `raise RuntimeError(f"LLM stream error: {msg}")`,不再靜默回空。真因第一時間浮出。
1. **`eval_search` 跑前 `wait_backend_ready()`**:用 trivial completion 輪詢後端(預設 40 次 × 3s),成功才開跑。撞不到 warmup window。線上常駐後端第一發即過,等於 no-op。

## 後果

- ✅ full-45 不再被 warmup window 早死污染;readiness guard + 乾淨單模型 → 穩定跑滿。
- ✅ 後端真掛(非 warmup)時,錯誤訊息直指 `LLM stream error: Couldn't connect`,不再是誤導的 `char 0`。
- ✅ 教訓:**先量再下結論**。空回應有多重成因(max_tokens / warmup / 後端掛 / 全 reasoning_content),逐一隔離,別跳到 VRAM。

## 操作守則(接本地單 slot 思考模型,接 ADR 0005)

1. `lemonade load` 回 success ≠ 後端 ready;**跑前輪詢一發 trivial call**。
1. 全 `unload` → 單 `load`,避免 `Max Models 2` 共存搶資源。
1. 診斷指令:`lemonade status`(已載 + Max Models)、`lemonade export <model>`(recipe ctx/args/backend)、`netstat -ano | findstr ":8001"`(subprocess 活否)、`lemonade backends`(本機裝 vulkan)。
1. lemonade 思考模型(moe-q4 / gemma-uc)輸出走 `reasoning_content` 而非 `content`;判官只需 0/1/2,prompt 引導出最終 JSON 即可,但累加器若要收思考需另接 `reasoning_content`。
