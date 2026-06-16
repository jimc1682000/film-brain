**繁體中文** | [English](README.en.md)

# film-brain — AI 影片庫大腦

語意化影片搜尋 + 多維度自動標籤。把扁平的片庫轉成 14 維 / 約 400 個標籤的彈性
taxonomy,讓人用「意思」找片(「想哭的時候看的」、「緊張的韓國驚悚片」),
而不是靠精準關鍵字。

為 CATCHPLAY+ Hackathon 2026 打造;開源出可直接跑、品牌中性的核心。

**線上 Demo / 技術說明:** https://jimc1682000.github.io/film-brain/

## 這個 repo 是什麼 — 跟線上 Demo 的差別

| 這個 repo(程式碼) | 線上 Demo(站台) |
| --- | --- |
| **可直接跑、以 mock 為基礎的核心** — FastAPI + NiceGUI + Qdrant + 本地 bge-m3 + cross-encoder。**免金鑰**:搜尋跑在本地模型,不需要 API key。用中性 seed 格式帶入你自己的片。 | 一個 **展示完整系統跑在真實 CATCHPLAY+ 目錄** 的作品集 — 搜尋重播、評測迭代故事、除錯案例。 |

所以站台展示的有幾件事 **刻意不隨此 repo 出貨**:
- **目錄 ingest / 爬蟲** 是 *私有的 source adapter*。此 repo 出的是 **通用載入器**(`scripts/seed_from_file.py`)+ **中性 adapter 範本**(`scripts/adapters/example_adapter.py`)+ 一份內附 **mock 資料集** — 用文件化格式(`data/films.seed.schema.json`)帶入你自己的片。
- **45-query 評測分數**(站上 nDCG@5 0.93 → 0.96)是在 *真實目錄* 上測的。此 repo 出 **同一套 harness**(`scripts/eval_search.py`)+ **同一組 45-query set**(`data/eval-queries.json` — 只放查詢字串、由 LLM 即時評、無 gold label),但這組數字要在真實目錄才復現;跑在內附 mock 片庫上同一套 harness 會得到不同分數。

系統 **不綁定 CATCHPLAY** — 任何符合 seed schema 的資料集都能跑。

## 快速開始(免金鑰、全容器化)

全部跑在容器裡 — qdrant、本地 **bge-m3** embedder(ollama,首次 `up` 自動拉取)、
backend、frontend。不需要 host Python、不需要 API key。

```bash
docker compose up -d        # qdrant + ollama(拉 bge-m3)+ backend + frontend
docker compose exec backend python -m scripts.seed_from_file data/films.seed.json
# 開 http://localhost:8080  (API 文件: http://localhost:8000/api/docs)
```

首次 `up` 會下載 bge-m3 權重(約 1.2 GB)到具名 volume,之後啟動瞬間完成。
雲端 LLM 金鑰(OpenRouter 等)**選用** — 放一份 `.env`(參考 `.env.example`)可
強化查詢理解 + 自動標籤;沒有的話這兩項優雅降級,搜尋照常運作。

### 用預先建好的 image(跳過本地 build)

`.github/workflows/image.yml` 會在每次 push 到 master 時把 `backend` / `frontend`
image 發佈到 GitHub Container Registry。把套件設為 **public** 之後
(GitHub → repo → Packages → 各 package → *Package settings* → 改 visibility 為
public),就能直接 pull 而不用本地 build:

```bash
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml exec backend \
    python -m scripts.seed_from_file data/films.seed.json
```

## 帶入你自己的片

放一份符合 `data/films.seed.schema.json` 的 `data/films.seed.json`(titles map、
taxonomy 標籤、選填 poster/year/country/cast),然後 `make seed`。標籤可用
`--auto-tag` 由 LLM 補齊。source-adapter 合約見 `scripts/adapters/example_adapter.py`。

## 架構

詳細 C4 + UML 圖在 [`docs/architecture/`](docs/architecture/):系統 context、
container、component、Protocol class diagram、hybrid-search pipeline + sequence、
資料模型(ERD)、部署。設計決策在 [`docs/adr/`](docs/adr/)。

## 技術

FastAPI · NiceGUI · SQLite · Qdrant · BAAI/bge-m3(本地)· bce-reranker cross-encoder ·
hybrid recall(vector + BM25/FTS5+jieba → RRF)· 誠實 cosine 分層計分 · OpenRouter-free / 本地 Ollama LLM。

## 授權

MIT — 見 [LICENSE](LICENSE)。
