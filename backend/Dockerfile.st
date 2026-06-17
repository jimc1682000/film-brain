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

# The slim base runs as the non-root `appuser`; switch back to root to install
# the extra layer, then drop privileges again below.
USER root
# CPU-only torch < 2.9 first — 2.9+ pulls the full CUDA stack (~3 GB) on CPU.
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu "torch<2.9"
COPY requirements-st.txt .
RUN pip install --no-cache-dir -r requirements-st.txt && chown -R appuser:appuser /app
# Re-declare non-root + healthcheck explicitly: a static scan (trivy DS-0002 /
# DS-0026) can't follow `FROM ${BASE}` to see the base image already set them.
USER appuser
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 CMD \
    python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"
