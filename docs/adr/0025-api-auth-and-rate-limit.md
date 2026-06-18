# ADR 0025 — 存取控制:search rate limit + admin 台 edge Basic Auth

- 狀態:Accepted
- 日期:2026-06-17
- 相關:[ADR 0019](0019-config-externalization-public-readiness.md)(config 外部化)、[ADR 0024](0024-prompt-injection-input-gate.md)(prompt-injection 閘道)、SECURITY.md、issue #39

## 背景

智慧片庫是 hackathon demo 的 prototype。未來進 prod 的形狀已釐清:

- **本 repo 的 NiceGUI 前端 = 內部 admin / tag 管理台**(server-side,用 httpx
  代呼叫 backend)。
- **search 能力經 API 對外**:未來由一個獨立的對外正式站消費,**normal-user 的
  認證做在那個對外站**(不在本 repo 範圍)。

所以本 repo 的存取控制只有兩件事:

| 對象 | 控制 |
| --- | --- |
| 進 admin / tag 管理台的人 | **edge HTTP Basic Auth**(反向代理擋在前端前) |
| search / awards API(內部 + 未來對外站打) | **per-IP rate limit**(防成本濫用 / DoS) |

現況起點:零 inbound rate limit;管理台只靠部署層 Basic Auth(VPS 既有,見下),
repo 內無體現。對外暴露的真風險:未授權寫入(tag/delete/ingest)、雲端 LLM 成本
濫用(auto-tag preview/re-tag、reanalyze)、search/rerank DoS(單機,無 HA)。

## 決策

### 1. search rate limit(已實作,PR1)

- **手刻 per-IP sliding-window 限流,做成 FastAPI dependency**
  (`backend/ratelimit.py` 的 `rate_limit_search`),掛在 search / similar 路由的
  `dependencies=[...]`。in-memory(單機現實,不需 Redis)。超限回 **429** + `Retry-After`。
- 限額 + 開關進 search-config(`rate_limit`:`enabled` / `limit` / `window_seconds`,
  hot-reload,_DEFAULTS fallback);`enabled:false`(預設)→ 不限(內網 demo / 測試不變)。
- **Proxy IP 修正(必做)**:backend 在反向代理後,uvicorn 加
  `--proxy-headers --forwarded-allow-ips=*`,否則 `request.client.host` 看到 proxy IP
  → 全 client 擠同一 bucket。信任 `*` 安全:只有 proxy 能到 backend(compose 內網)。
- **為何手刻而非 slowapi**:slowapi 的 `@limiter.limit` 強制路由要有 `request: Request`
  參數,但 `semantic_search` 被 lifespan warmup **直接呼叫** + 兩處測試直接呼叫——加參數
  得重構路由/lifespan/測試。dependency 只在 HTTP 路徑跑、零波及;沿用 [ADR 0024](0024-prompt-injection-input-gate.md)
  prompt_guard 的手刻先例。fixed/sliding window per-IP 對單機 prototype 的成本/濫用/DoS 已足夠。

### 2. admin 台認證 — edge Basic Auth(乾淨分離,app 零改動)

管理台是低流量內部單一服務,要的是「擋一個 console」而非角色系統 → **HTTP Basic Auth
擺在反向代理 edge**,app 完全不碰 auth。repo 提供 **Caddy** 作為可選 edge:

- **`Caddyfile` + `docker-compose.caddy.yml`(opt-in overlay)**:Caddy 擋在前端前,
  `basic_auth`(bcrypt;`caddy hash-password` 產)→ reverse_proxy frontend。creds 走 env
  (`BASIC_AUTH_USER` / `BASIC_AUTH_HASH`),**不在 repo**。
- **auto-TLS 切換**:`CADDY_SITE_ADDRESS` = hostname → Caddy 自動 LE 憑證(**public demo
  預設**,TLS 免費);`:80` → 純 HTTP,跑在已終止 TLS 的代理後(如 VPS Traefik)。
- **implementer 自選**:用這個 overlay、用自己的代理、或不擋——repo 給範例,不強迫。
- **為何 Caddy(非 Traefik/Nginx)**:單一靜態服務擋 Basic Auth,Traefik 的動態 service
  discovery 用不到;Caddy 設定最簡 + **有 `caddy fmt` / `caddy validate` 工具鏈**(進
  pre-commit + CI gate,容器化跑,免裝 binary),Traefik 動態 label 無離線 validate。

### 3. backend admin 防線 — 內網只(縱深)

edge Basic Auth 擋「進管理台的人」,但擋不了直打 backend admin endpoint(對外站只該拿到
search API)。所以 backend 的 admin 路徑(auto-tag、awards ingest、films DELETE、reviews
POST、feedback reanalyze)由**反向代理只路由公開路徑(search/films/tags/awards)、admin
路徑不對外**——網路隔離即防線。(部署層設定,記在 runbook。)

### 4. 部署 topology

```
public demo(repo 預設,standalone):  Caddy(Basic Auth + auto-TLS)→ frontend → backend
VPS(已有 Traefik):                  Traefik(TLS + 路由)→ Caddy(Basic Auth)→ frontend → backend
                                      Traefik /api(search)→ backend(rate-limited)
對外正式站(未來):                    自己的 normal-user auth → 打本服務的 search API
```

VPS 既有設定:Traefik(docker-provider、LE)已用 label 對 demo 做 Basic Auth(含 trusted-IP
bypass)。導入 Caddy 後改為 Traefik → Caddy → fe,Basic Auth 移到 Caddy。真實 cred / 內部
IP / hostname 留 VPS-local overlay,**不進公開 repo**。

## 取捨

- **edge Basic Auth vs app 內 OAuth**:管理台是內部單一 console,Basic Auth「先行」最省;
  OAuth(GitHub/Google、角色 allowlist)留作未來——且 normal-user 認證本就屬未來對外站,
  不在本 repo。先不背 OAuth app 註冊 / session / JWT 的成本。
- **Caddy 進 repo vs 只靠 VPS Traefik**:Caddy 讓 auth pattern **在 repo 內可見、可攜、可測**
  (fmt/validate gate),implementer 不必有 Traefik 也能自成一套 gated demo;VPS 真值仍留
  部署層不外洩。代價:VPS 上 Traefik → Caddy 兩層(可接受)。
- **手刻 rate limit vs slowapi**:見 §1(路由直接呼叫的整合摩擦)。
- **盲點**:單一 Basic Auth credential 無 per-user 稽核 / 角色;in-memory limit 不跨實例。
  皆隨「單機 / 單 operator / 內部管理台」現實可接受,scale 或對外正式站再升 OAuth。

## 結果

- **rate limit**:`backend/ratelimit.py` 上線,預設關(內網/測試不變),外部部署開。
- **admin 台 auth**:repo 提供 Caddy opt-in edge(Basic Auth + auto-TLS),`caddy fmt` /
  `caddy validate` 進 pre-commit + CI;VPS 用 Traefik → Caddy。
- **未來**:對外正式站消費 search API、做 normal-user 認證;需要角色 / SSO 時升 OAuth/OIDC。
- 文件:SECURITY.md(姿態 + edge auth)、runbook(部署 topology + env)、`.env.example`。
- 範圍:PR1 = rate limit(已 merge);PR2 = Caddy edge + 工具鏈 gate + 文件(本次)。
