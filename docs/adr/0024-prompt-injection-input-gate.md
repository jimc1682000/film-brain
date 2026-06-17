# ADR 0024 — 分層 Prompt-Injection 輸入閘道(OWASP LLM01)

- 狀態:Accepted
- 日期:2026-06-17
- 相關:[ADR 0019](0019-config-externalization-public-readiness.md)(config 外部化)、[ADR 0022](0022-security-hardening-ssrf-and-local-sast.md)(SAST)、[ADR 0023](0023-slim-container-images.md)(slim image)、SECURITY.md(OWASP LLM Top 10)

## 背景

query expansion 把**使用者查詢**餵給 LLM(auto-tag 餵 metadata)。我們本來只在
**輸出端**防禦(tags 對 registry 驗證、JSON schema)——這擋住「注入無法逃逸成
code/SQL」,但缺**輸入端**的 prompt-injection 閘道(OWASP LLM01,排名第 1)。

現成 lib(`llm-guard` 的 `PromptInjection`)是 **transformer 模型**
(deberta ~400MB),需要 torch/transformers——正是 [ADR 0023](0023-slim-container-images.md)
為了瘦身移出的東西,且每查多一次 CPU 推論。直接全用會打回瘦身成果。

## 決策

**分層(cheap 規則優先,ML 確認 gray zone)**:

- **Layer B(always-on,含 slim)**:`backend/services/prompt_guard.py` 的
  regex + 啟發式閘道(`inspect`)。零依賴、~0.2ms、zh/en/ja。
  - 三級:**SAFE** 放行 / **BLOCK** 拒(`expand_query` 跳 LLM、降級 BM25,
    **不硬失敗搜尋**)/ **SUSPICIOUS** gray zone。
  - **SUSPICIOUS 刻意拉寬**(「寧誤殺不漏殺」):成本低——slim 只 log+放行
    (不會 false-BLOCK),ST 升級給 llm-guard 清 FP。BLOCK 保守(會降級搜尋)。
- **Layer A(ST optional)**:`llm-guard`(`requirements-st.txt`)。**只在
  SUSPICIOUS 時**呼叫(`inspect_deep`)確認:injection → BLOCK、clean → SAFE。
  lazy import,沒裝(slim)→ 回 None → SUSPICIOUS 維持(log+放行)。
- **門檻/權重外部化**到 search-config(`prompt_guard` 區塊,_DEFAULTS fallback,
  hot-reload)——照 [ADR 0019](0019-config-externalization-public-readiness.md),不用 deploy 就能調。

## 取捨

- **手寫 B vs 純 lib**:純 llm-guard 會把 torch/transformers 加回所有 image
  (反 ADR 0023)+ 每查推論延遲。手寫 B 零依賴/快/fits-slim(即文章的 L1/L2),
  lib 只當**升級層**——兼顧「用 lib」與「slim」。
- **盲點誠實**:regex/啟發式擋不住語意層 / 多輪累積 / 間接(工具輸出)注入;
  llm-guard 補一部分;`expand_query` 的輸出驗證 + degrade 是最後底線。見 SECURITY.md。
- **為何不只靠輸出驗證**:縱深防禦——輸入閘道(本 ADR)+ 既有輸出驗證雙層。

## 結果

slim 有 always-on 的 cheap gate(BLOCK→降級、SUSPICIOUS→log);ST 多 llm-guard
ML 確認。門檻可在 search-config 調。測試:`test_prompt_guard.py`(L1/L2 三級
+ 真實片查詢無 FP + llm-guard 升級 mock + expand_query BLOCK 不呼叫 LLM)。
