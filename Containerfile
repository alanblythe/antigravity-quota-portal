# Multi-stage Container Build
# Stage 1: Build Frontend assets
FROM docker.io/library/node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --prefer-offline || npm install
COPY frontend/ ./
RUN mkdir -p /app/static && npm run build

# Stage 2: Production Python Cloud Run Container
FROM docker.io/library/python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

# Copy dependency definition
COPY pyproject.toml ./

# Install Python dependencies into system environment
RUN uv pip compile pyproject.toml -o /tmp/requirements.txt && \
    uv pip install --system --no-cache -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt /bin/uv

# Copy application source code and configuration
COPY config/ ./config/
COPY app/ ./app/

# Copy compiled frontend from Stage 1
COPY --from=frontend-builder /app/static/ ./app/static/

# Cloud Run defaults
ENV PORT=8080
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1
ENV USE_MOCK_SERVICES=false

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT:-8080}/api/health || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8080}"]
