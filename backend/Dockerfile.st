# Backend + sentence-transformers (CrossEncoder reranker, ADR 0009), layered ON
# TOP of the slim image so it reuses slim's base layers — the registry stores
# the shared base once and this adds only the torch/ST delta (~600MB).
#
# BASE must point at a slim image that already exists (built+pushed first; the
# image.yml `backend-st` job depends on the `base` job). Local build:
#   docker build -f backend/Dockerfile -t fb-backend:slim --target slim .
#   docker build -f backend/Dockerfile.st --build-arg BASE=fb-backend:slim -t fb-backend:st .
ARG BASE=ghcr.io/jimc1682000/film-brain-backend:master
# BASE's default is tagged (:master); the ARG just isn't statically resolvable.
# hadolint ignore=DL3006
FROM ${BASE}
WORKDIR /app # explicit (the base sets it too) — keeps the COPY below unambiguous

# CPU-only torch < 2.9 first — 2.9+ pulls the full CUDA stack (~3 GB) on CPU.
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu "torch<2.9"
COPY requirements-st.txt .
RUN pip install --no-cache-dir -r requirements-st.txt
