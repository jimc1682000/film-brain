# ADR 0019 — 內部值外部化(public-readiness 的 config 紀律)

- 狀態:Accepted
- 日期:2026-06-13
- 相關:[ADR 0003](0003-tuning-config-and-explainability.md)(可調設定集中化)

## 背景

準備把這個 prototype 開源。Code 裡夾了**內部專屬的硬編值**,最明顯的是
`backend/llm_client.py` 把 OpenRouter free-tier attribution 的 `HTTP-Referer`
寫死成內部 demo VPS 的 host。這類值:

- 公開後會洩漏內部基礎設施位置;
- 對任何 fork 的人毫無意義(是我們 demo 機的位址)。

## 決策

**任何內部專屬值一律抽到 `settings` / env,並給 public-safe 的預設(空字串或通用值)。**

- `openrouter_referer` / `openrouter_title` 進 `config.py`,預設**空字串 / 通用名**;
  `llm_client` 改讀 settings(空值無害,header 照送)。
- `.env.example` 補齊**所有** runtime 需要的 key/開關(OpenRouter / Gemini /
  Anthropic / TMDB / Qdrant / LLM_BACKEND / TAGGING_CLOUD_BACKEND),不再只列三個。
- Secrets 永遠走 `.env`(gitignored),repo 內**不得**出現真實 key、內部 host、
  內部 IP、員工信箱、1Password 連結。
- pre-commit 的 `betterleaks` hook 是這條的機制守門(機制 > 提醒)。

## 原則

這是 ADR 0003「設定集中化」+「機制才是規則」的延伸:**內部 vs 公開的差異要靠
config 邊界處理,不靠記得在發布前手動改 code**。預設值本身就是 public-safe 的。

## 後果

- 公開版與內部部署用同一份 code,只差 `.env`。
- 內部 referer 等值,部署時在 VPS 的 env 設定即可,不回寫 code。
