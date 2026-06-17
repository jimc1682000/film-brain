# NiceGUI frontend — talks to the backend over HTTP, so it installs ONLY the
# frontend deps (requirements-frontend.txt), not the ML/vector/DB stack. Deps
# install into /install and are copied in, so pip/build caches never ship.

FROM python:3.11-slim AS deps
WORKDIR /app
COPY requirements-frontend.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements-frontend.txt

FROM python:3.11-slim AS runtime
WORKDIR /app
COPY --from=deps /install /usr/local
COPY frontend/ ./frontend/
# Non-root (trivy DS-0002). The frontend writes nothing to disk, so no volume
# permission concerns.
RUN useradd --uid 1000 --create-home appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8080
# Liveness — NiceGUI serves the root page; no curl in slim, probe with urllib
# (trivy DS-0026).
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 CMD \
    python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/', timeout=2).status == 200 else 1)"
CMD ["python", "-m", "frontend.app"]
