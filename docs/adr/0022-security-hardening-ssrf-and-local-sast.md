# ADR 0022 — 安全強化:SSRF guard + 本地 SAST

- 狀態:Accepted
- 日期:2026-06-16
- 相關:[ADR 0019](0019-config-externalization-public-readiness.md)(public-readiness)、[ADR 0021](0021-boundary-protocols-and-decoupling.md)

## 背景

repo 公開後啟用 GitHub code scanning(CodeQL)+ Dependabot。CodeQL 報出
真實安全發現(1 critical SSRF、1 high SQLi、數個 log-injection),且我們想要
**commit 時就能跑的本地 SAST**(CodeQL 只在 CI、且慢)。兩件事一起處理。

## 決策

### 1. SSRF guard(`backend/tmdb_lookup.py`,CodeQL #1 critical)

`catchplay_poster` 拿 user-supplied URL(create-film API)做 `httpx.get(...,
follow_redirects=True)` → SSRF(可探內網 / 公開 URL 轉址進內網)。

- **通用化為 `og_image`**(brand-neutral,符合 bring-your-own-films),不鎖
  `catchplay.com`(host-allowlist 會破壞中性設計)。
- **手刻 SSRF guard**(`_is_safe_url` / `_host_is_public`):限 http(s)、拒絕
  解析到 private/loopback/link-local/reserved IP(含 169.254.169.254 雲
  metadata)、**逐跳驗證 redirect**(關掉自動 follow,手動跟並每跳重驗)。
  選手刻而非 `httpx-secure`(免 +1 dep);這正是 OWASP SSRF cheat-sheet 的標準
  寫法。DNS-rebinding(resolve→reconnect 換 IP)不在守備範圍 —— 本系統設計給
  local/trusted 跑(見 SECURITY.md)。
- CodeQL taint 模型 **不認得 IP-resolution guard 是 sanitizer** → 修好後仍
  重報 → **dismiss as false positive**(附理由)。

### 2. 本地 SAST:ruff `S` + semgrep,CodeQL 當 oracle

- **ruff `S`(flake8-bandit,pattern-based)**:用已在 pre-commit + CI 的工具,
  零新依賴。
- **semgrep(taint-based)**:補 ruff pattern 抓不到的資料流。
- **triage 用 CodeQL 當 oracle**:ruff S 的 8 個 S608(SQL f-string)只有
  CodeQL taint 確認的那個值得查 —— 確認是 column 名程式固定、值已參數化的
  **誤報** → 全域 ignore S608 並附理由(真 SQLi 交給 taint 工具)。同理 ignore
  S110/S112(故意降級)、S104(container 故意 bind)。
- **真信號修掉**:`hashlib.md5(..., usedforsecurity=False)`;NiceGUI
  `storage_secret` 由硬編字面值改 `os.getenv` 可覆寫。
- **semgrep 的 httpx 缺口**:免費 registry 的 SSRF 規則 **不涵蓋 httpx**
  (實測 blatant `httpx.get(user_url, follow_redirects=True)` = 0 findings)→
  自訂 `.semgrep/httpx-ssrf.yml`,**驗證它對 vuln pattern 觸發、對 guarded
  `_safe_get` 不誤報**。
- **分工**:pre-commit 跑本地 `.semgrep/` 規則(changed files,快、不抓 registry);
  CI 跑 `p/security-audit` + 自訂規則(全 tree,`--error`)。
- tests 整類 ignore S(assert/fixture);scripts ignore S603/S607(可信 CLI)。

## 取捨

- **pattern(ruff S)vs taint(semgrep/CodeQL)**:ruff 快/本地但會把安全的
  parameterized SQL 一起報 → 用 taint 工具當真偽裁判,pattern 噪音類全域 ignore。
- **CodeQL FP**:正確的 fix 反被 CodeQL 重報(不認自訂 sanitizer)。寫
  CodeQL sanitizer extension 過重 → 選 dismiss + semgrep 自訂規則在本地補位。
- **httpx 自訂規則**:免費 registry 漏掉 httpx,所以「真 bug 出過的地方」必須有
  本地 gate,不能假設 registry 有覆蓋。

## 結果

CodeQL #1 SSRF 修復 + dismiss;ruff S + semgrep 進 pre-commit + CI(目前
0 findings,`--error` gate);tests 全程 mock,CI 不需真 torch/ST。
