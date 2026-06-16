# ADR 0023 — 精簡容器 image(ST 疊在 slim 上)

- 狀態:Accepted
- 日期:2026-06-16
- 相關:[ADR 0009](0009-confidence-tiers-and-honest-scoring.md)(CrossEncoder 排序)、[ADR 0019](0019-config-externalization-public-readiness.md)(public-readiness)

## 背景

backend / frontend 的容器 image 都 ~1.42GB。主因:

- `torch` + `sentence-transformers`(~1GB)。但這兩個只服務 **兩個非必要路徑**:
  (a) 可選的 sentence-transformers embedding backend(預設走 Ollama),
  (b) CrossEncoder reranker(ADR 0009)。兩者都是 **lazy import**,沒裝也不會
  在 import 期炸。
- **frontend(NiceGUI)根本不需要** torch / qdrant / ML stack —— 它只透過 HTTP
  打 backend,卻照單全收 `requirements.txt`。

## 決策

**把 torch/ST 從預設 image 拆出,並用「疊層」而非「平行重建」提供完整版。**

1. **依賴拆分**:`sentence-transformers` 移到 `requirements-eval.txt` 之外的
   `requirements-st.txt`(torch 由 CPU index 先裝);frontend 用最小
   `requirements-frontend.txt`(nicegui + httpx)。

2. **backend 兩個 image**:
   - `film-brain-backend`(slim,~480MB):Ollama embedding + RRF 排序。
   - `film-brain-backend-st`:用 `backend/Dockerfile.st` **`FROM` slim image**
     再裝 torch/ST。→ registry **共用 slim base 層**,st 只多 ~600MB delta,
     不重複 base(對比「full 從頭重建」會多存一份 ~480MB)。

3. **multi-stage**:deps 裝到 `--prefix=/install` 再 `COPY` 進最終 image,
   pip/build cache 不隨 image 出貨。

4. **CI(image.yml)兩階段**:`base` job 先 build+push slim + frontend;
   `backend-st` job `needs: base`,`Dockerfile.st` `FROM` 剛推的 slim
   (tag 由同一份 metadata-action 推導,跨 branch/tag 一致)。multi-arch
   (amd64+arm64)由 manifest-list 處理。

5. **compose 預設**:本地 build compose 走 slim;完整 CE 品質用預先建好的
   `-st` image(GHCR compose 註明)。

## 取捨

- **slim 預設 = RRF 排序**(無 CE)。CE 是 ADR 0009 的排序品質核心,所以 slim
  的排序品質較低。接受理由:預設 image 小而快,要完整品質的人換 `-st`(一行)。
  「正確 vs 大小」由使用者自選,而非綁死在一個 1.4GB image。
- **st `FROM` slim 的代價**:CI 要分兩階段(st 等 slim 先 push)+ `Dockerfile.st`
  耦合一個已發布的 tag。換來 registry 不重複存 base、有 slim cache 時升級只拉
  delta。在 GHCR 儲存/拉取效率上勝過「full 從 base 重建」。
- **ST 仍是 lazy import**,且在 slim 環境缺席 → 在兩個 import 點標
  `# pyright: ignore[reportMissingImports]`(typecheck 不裝 ST 也綠)。測試全程
  mock ST/CE,CI 不需真 torch。

## 結果

本地實測:frontend 1.42GB → **314MB**,backend slim 1.42GB → **479MB**,
st `FROM` slim 正常 build。
