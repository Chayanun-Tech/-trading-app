# Hugging Face Spaces (Docker SDK) — AI Trade Assistant
# Builds FastAPI backend + serves single-page frontend on port 7860.
FROM python:3.11-slim

# System deps (build tools for some wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# App code
COPY backend ./backend
COPY frontend ./frontend

# HF Spaces runs as non-root uid 1000; give it ownership
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PORT=7860
EXPOSE 7860

WORKDIR /app/backend
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
