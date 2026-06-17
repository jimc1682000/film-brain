# Slim backend: Ollama embedding + RRF ranking — no torch/sentence-transformers
# (~480MB). The optional CrossEncoder reranker (ADR 0009) is layered on top by
# backend/Dockerfile.st (FROM this image), so it reuses these base layers
# instead of duplicating them. Deps install to a prefix and are copied in, so
# pip/build caches never ship.

FROM python:3.11-slim AS deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim AS slim
WORKDIR /app
COPY --from=deps /install /usr/local
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY data/ ./data/
# Run as non-root (trivy DS-0002). uid 1000 is the typical first host user, so a
# bind-mounted ./data owned by that user stays writable; for a different host
# uid, override with `user:` in compose or chown the host dir (see runbook).
RUN useradd --uid 1000 --create-home appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
# Liveness via /health (returns immediately — warmup is backgrounded). No curl
# in the slim image, so probe with stdlib urllib (trivy DS-0026).
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 CMD \
    python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
