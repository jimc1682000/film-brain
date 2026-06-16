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
EXPOSE 8080
CMD ["python", "-m", "frontend.app"]
