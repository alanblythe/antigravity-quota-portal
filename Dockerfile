# Multi-stage Container Build
# Stage 1: Build Frontend assets
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --prefer-offline || npm install
COPY frontend/ ./
RUN npm run build || true
RUN mkdir -p /app/static && if [ -d dist ]; then cp -r dist/* /app/static/; fi

# Stage 2: Production Python Cloud Run Container
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency definition
COPY pyproject.toml uv.lock* ./

# Install Python dependencies into system environment
RUN uv pip compile pyproject.toml -o /tmp/requirements.txt && \
    uv pip install --system --no-cache -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Copy application source code
COPY app/ ./app/

# Copy compiled frontend from Stage 1
COPY --from=frontend-builder /app/static/ ./app/static/

# Cloud Run defaults
ENV PORT=8080
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
