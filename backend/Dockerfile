FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# CPU-only torch < 2.9 first; torch 2.9+ declares cuda-toolkit as a runtime
# dep that pulls the full CUDA stack (~3 GB) even on CPU hosts.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch<2.9"
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY data/ ./data/

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
