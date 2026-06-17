# ADR 0025 — 存取控制:rate limit(現在)+ OIDC 登入(階段 B)

- 狀態:Accepted
- 日期:2026-06-17
- 相關:[ADR 0019](0019-config-externalization-public-readiness.md)(config 外部化)、[ADR 0024](0024-prompt-injection-input-gate.md)(prompt-injection 閘道)、SECURITY.md、issue #39

## 背景

智慧片庫是 hackathon demo 的 prototype。存取控制需求**依部署模式分階段**:

| 模式 | 對象 | search / awards(讀) | tag(寫 + LLM) | 需要的控制 |
| --- | --- | --- | --- | --- |
| **A — 內網 demo(現在,預設)** | 公司內 user | 直接用 | 直接用 | **只 rate limit** |
| **B — 外部服務(未來)** | GitHub/Google 登入的 normal user | 登入後可用 | ✗ | **登入牆(OAuth)** |
| | 特定 admin(內部) | — | 限 admin | **admin allowlist** |

現況:零 authn/authz、零 inbound rate limit。所有 endpoint 全開,含寫入
(auto-tag save/create、awards ingest、films delete、reviews)與燒雲端 LLM token
的(auto-tag preview/re-tag、feedback reanalyze);search 還吃 query expansion LLM
+ CPU rerank(~7s)。

對外暴露的真風險:未授權寫入、雲端 LLM 成本濫用、search/rerank DoS(單機,無 HA)。

## 決策

### 0. 一個總開關,預設 = 階段 A

`AUTH_ENABLED`(env,預設 `false`)。`false` → 無登入、僅 rate limit(內網 demo
行為**完全不變**,測試 / synthetic demo 全綠)。`true` → 階段 B 登入牆生效。
不為「還沒到的階段」加每請求硬成本 —— opt-in,設了才強制(沿用本專案
「degrade don't crash / 本地可跑」原則)。

### 1. Rate limit(現在就做,兩階段都需要)

- **手刻 per-IP fixed-window 限流,做成 FastAPI dependency**(`backend/ratelimit.py`
  的 `rate_limit_search`),掛在 search / similar 路由的 `dependencies=[...]`。in-memory
  (單機現實,不需 Redis)。超限回 **429**。
- 限額 + 開關進 search-config(`rate_limit` 區塊:`enabled` / `limit` / `window_seconds`,
  hot-reload,_DEFAULTS fallback);`enabled:false`(預設)→ 不限。
- **Proxy IP 修正(必做)**:backend 在 Traefik 後,uvicorn 加
  `--proxy-headers --forwarded-allow-ips=*`,否則 `request.client.host` 看到 proxy IP
  → 全 client 擠同一 bucket。信任 `*` 安全:只有 proxy 能到 backend(compose 內網)。
- **為何手刻 dependency 而非 slowapi**(實作時翻盤,記錄理由):slowapi 的
  `@limiter.limit` 強制路由要有 `request: Request` 參數,但 `semantic_search` 被 lifespan
  warmup **直接呼叫**(`main.py` 的 demo-chip 預熱)+ 兩處測試直接呼叫——加 request 參數
  會逼著重構路由/lifespan/測試。dependency 只在 HTTP 路徑跑、不影響直接呼叫,零波及;
  且沿用 [ADR 0024](0024-prompt-injection-input-gate.md) prompt_guard 的手刻先例。
  fixed-window per-IP 對單機 prototype 的「成本/濫用/DoS」防護已足夠(slowapi 預設也是
  fixed-window)。

### 2. 階段 B 登入 — BFF pattern,OIDC 放前端(scaffold,預設關)

人在 **NiceGUI 前端**(server-side,用 httpx 代呼叫 backend)→ 它天生是
**Backend-For-Frontend**。所以 OIDC 放前端;前端→後端是 service-to-service。
(這是現代瀏覽器 app 的標準做法:session 留在前端 server、token 不落瀏覽器。)

- **登入**:Authlib + NiceGUI;**GitHub + Google 雙 provider**(Authlib 多 provider 註冊
  —— Google 走 OIDC discovery;GitHub 是 OAuth2,需 `user:email` scope 取 email)。session
  走 signed cookie(`SESSION_SECRET`)。每 provider 一組 client id/secret
  (`GITHUB_CLIENT_ID`/`_SECRET`、`GOOGLE_CLIENT_ID`/`_SECRET`),登入頁列出兩個按鈕。
- **normal user**:任何成功經 GitHub / Google 登入的帳號(登入本身即讀取的門檻;
  不限網域)。可:search、awards。
- **admin**:`AUTH_ADMIN_EMAILS` env allowlist(逗號分隔,比對 provider 回傳的 email)。
  命中才開 tag UI。不用 OIDC group/claim(prototype 最簡;日後 scale 再升 group)。
- **授權強制點**:前端依 登入狀態 + 角色 控制頁面/動作(公開頁 search/awards;
  admin 頁 tag);**只有前端**(帶 service token)會呼叫 backend admin。

### 3. 後端 admin 防線 — service token(+ 內網只)

- backend admin endpoint(auto-tag preview/create/{id}/save/accept、feedback
  reanalyze、awards ingest、films DELETE、reviews POST)要 `BACKEND_SERVICE_TOKEN`
  (前端 env 帶,**human 永不手動碰** —— 這就是先前「嫌麻煩的 key」搬到看不見的這一跳)。
  `secrets.compare_digest` 常數時間比對;token 空(階段 A)→ 放行。
- **理想**:Traefik 只路由公開路徑(search/films/tags/awards)到 backend,admin 路徑
  不對外 —— service token 變縱深第二層。(部署層設定,記在 runbook。)

## 取捨

- **現在只實作 rate limit + scaffold auth(預設關)**:auth 是階段 B,但使用者要先把
  結構做起來。預設關 → 階段 A 零影響;階段 B 翻 flag + 設 env 即可,不必回頭改架構。
- **BFF vs oauth2-proxy vs gateway-JWT**:選 BFF —— 在 repo 內、可攜(proxy 那套在部署層、
  公開 repo 看不到,而 repo 是交付物)、天然支援「公開讀 + 角色化寫」混合。
- **service token vs 轉發 human OIDC JWT 到後端**:選 service token —— admin 只走 UI
  (已確認),後端不需懂 human session;轉發 JWT 是無收益的複雜度(驗證 / refresh / 傳遞)。
- **static key(被否決)的去向**:沒浪費,**搬到前端→後端那跳**當隱形 service 憑證。
- **盲點**:單 admin allowlist 無 per-caller 稽核 / 撤銷粒度;in-memory limit 不跨實例;
  service token 單一共享。皆隨「單機 / 單 operator / prototype」現實可接受,scale 時再升級。

## 結果

- **現在**:rate limit 上線(兩階段都用);auth 全 scaffold 但 `AUTH_ENABLED=false`
  → 內網 demo 行為不變。
- **階段 B(翻 flag + 設 env)**:外部 normal user OIDC 登入後可 search/看 awards;
  tag 操作限 `AUTH_ADMIN_EMAILS` 的 admin;backend admin 由 service token(+ 內網只)守。
- 文件:SECURITY.md 姿態(A/B 模式)、runbook(env + Traefik 路由)、`.env.example`。
- 測試:rate limit 超限→429;`AUTH_ENABLED=true` 下 未登入→擋、normal→只讀、admin→可 tag;
  預設關 → 全綠不破。
- 範圍:前端 + 後端跨服務功能,預計分 PR(rate limit 一支;auth scaffold 一支)。
