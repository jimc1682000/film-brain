FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frontend/ ./frontend/
# Tech brief rendered at /brief — keep the committed doc in the image.
COPY docs/judge-brief.md ./docs/judge-brief.md

EXPOSE 8080

CMD ["python", "-m", "frontend.app"]
