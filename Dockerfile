# ── Stage 1: build React frontend ────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# Output lands in /build/app/static (per vite.config.ts outDir)


# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.12-slim

# System deps: chromium + chromedriver for Selenium, ffmpeg for audio
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY echo360/ ./echo360/
COPY app/ ./app/

# Copy built frontend assets from stage 1
COPY --from=frontend-builder /build/app/static ./app/static/

# Data volumes: database and audio library live outside the container
ENV ECHO360_DB=/data/echo360.db
ENV ECHO360_AUDIO_DIR=/data/audio

EXPOSE 8742

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8742"]
