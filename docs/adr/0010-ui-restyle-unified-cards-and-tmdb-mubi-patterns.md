# ADR 0010 — UI 改版:統一卡片元件、品牌色系一致、TMDb / MUBI 版型

- 狀態:Accepted
- 日期:2026-06-09
- 延續:[ADR 0009](0009-confidence-tiers-and-honest-scoring.md)(三段信心帶 → 本次的分數 badge 配色)
- 相關:Smart Interface Design Patterns《Badges/Chips/Tags/Pills》、TMDb awards 頁、MUBI 影片頁

## 背景

demo 各頁的清單視覺各自長成:搜尋是 film_card、相似片另一套、awards 又一套;分數標示一版一版改
(長條 → 藥丸 → 折角);awards 頁是扁平 accordion。缺乏**單一元件**與**單一品牌語言**,改一處不影響另一處,
反覆出現「不一致、沒整體感」。同時要參考業界版型(TMDb 獎項、MUBI 影片頁)提升質感。

## 決策

### 1. 單一清單元件 `film_list.render(films, style, *, tiered)`

搜尋、相似片、browse、awards 命中片/分類列**全部走同一個 renderer**。`style` 由全域下拉切換,
只留 **`default`(film_card)** 與 **`bubble`**(grid 併入 bubble,一種乾淨海報面板)。`tiered=False`
給無相似度排序的 awards(等大);`tiered=True` 給搜尋/相似(桌機 3 階、手機 2 階大小)。
film dict 可帶 award 欄位:`won`(金邊)、`badge`(狀態 chip)、`reason`(覆寫匹配理由)。

### 2. 狀態 badge:深色玻璃底 + 不透明文字(不被海報染色)

分數 % 與 得獎/提名共用一個**靜態 badge**(右上角)。半透明**色底會跟海報混色**(綠×紅底→濁黃),
故改 **深色半透底(`rgba(16,16,16,.74)`)+ blur**,顏色只走**不透明文字**(score 三色帶沿用 ADR 0009 語意:
≥70% 綠 / 40–70% 黃 / \<40% 紅,柔和色)。badge=靜態指示,不做成可點外觀(對齊 Badges/Chips 原則)。

### 3. 互動:原生 `<a>` link + 純 CSS `:hover`(零 JS state)

卡片本身是 `ui.link`(原生 `<a>`)。揭露 overlay 用 CSS `:hover`:
**桌機**一鍵直接進詳情(hover 顯示資訊);**觸控**首觸 = `:hover` 顯示、次觸跟連結。
`:hover` 自動清除 → 不卡住、不會多張同時開。曾用手動 JS `fl-show` flag → 不清除、與 hover 衝突,已棄。

### 4. 品牌色系一致(去彩虹)

新表面一律取既有 tokens:`#000` bg / `#1f1f1f` 卡 / `#262626` 邊 / `12px` 圓角 / `#f26f21` 單一橘 accent。
awards 卡曾用 12 色漸層 → 不協調,改**統一深色卡**,差異靠內容(logo / 片庫覆蓋 chip)非顏色。

### 5. 詳情頁 MUBI 風 hero(全幅 backdrop)

非預設風格:全幅 cinematic hero(脫離 max-width 欄,避免裁切 100vw)。背景優先 **TMDb backdrop(w1280)**,
沒有則用**模糊放大海報** fallback(w500 直幅硬拉會糊)。hero 只留 標題(中/英)+ TMDb 評分 + 觀看鈕 + TMDb#;
導演/主演/類型移到下方一組;劇情只在下方一次(不與 hero 重覆)。標籤信心改 **SVG 圓環 + 貪婪 4 欄 masonry**。

### 6. awards 兩層(TMDb 風)

`/awards` = 獎項卡 grid(每獎一張深色卡:**TMDb logo 滿版**或**金色獎杯 SVG 徽章** fallback + 片庫覆蓋數,
依覆蓋排序);點卡 → `/awards/{org}` = 全幅漸層 hero + 該獎典禮(命中片 rail + 依類別列,泡泡時走 film_list)。
獎杯徽章用 **CSS background `center / 50%`** 置中(`ui.image`/`ui.html` 的 wrapper 會讓 `%` 尺寸跑掉)。

### 7. backdrop backfill

`films.tmdb_backdrop_url`(w1280)+ `scripts.enrich_backdrops`(idempotent,加欄 + 有 tmdb_id 才撈,試 movie 再 tv)。
deploy 後在 VPS 跑一次;local/VPS 各約 **569/649** 部取得 backdrop,其餘 TMDb 無。

## 重要踩雷

- **deferred load 的 `add_head_html` 不進已送出的 `<head>`**:film_list 在 awards 典禮 / 相似片的
  `ui.timer` 延遲載入裡注入 CSS → 沒套用 → 卡片裸樣式堆疊。改在 `header()`(page setup)同步注入。
- **deferred load 撞快速導覽** → `RuntimeError: parent slot deleted`:async load 寫入已刪除 slot。
  以 `try/except RuntimeError` 守衛(awards 典禮 + 相似片)。
- **TMDb 無 awards API**:獎項提名資料靠自有 ingest + Wikidata 驗證;TMDb 只做已比對片的 enrich(海報/backdrop)。
  awards logo 取自 TMDb popular award 頁(7 個有,其餘獎杯徽章 fallback)。

## 後果

- 改一個元件 / 一組 token,全站清單同步;新表面有明確規範可循。
- 風格下拉可即時切 default ↔ bubble 比較,降低改版風險。
- 代價:film_list 承載搜尋 + awards 兩種資料形狀(以 `badge`/`won`/`reason` 區隔),需留意別過度耦合。
