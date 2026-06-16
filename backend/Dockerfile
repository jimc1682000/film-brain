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
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
