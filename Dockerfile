# Stage 1: frontend build
FROM node:20-slim AS frontend-builder

WORKDIR /build
COPY frontend/ frontend/
RUN cd frontend && (npm ci --ignore-scripts 2>/dev/null || npm install) && npm run build
# vite.config.ts outputs to ../backend/UI/Web → /build/backend/UI/Web/

# Stage 2: python dependencies
FROM python:3.11-slim AS py-builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 3: runtime
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY --from=py-builder /install /usr/local

# Create non-root user
RUN useradd --uid 1000 --no-create-home --shell /bin/false swarm

WORKDIR /app

# Copy application code
COPY --chown=swarm:swarm backend/ backend/
COPY --chown=swarm:swarm config/ config/
COPY --chown=swarm:swarm scripts/ scripts/
COPY --chown=swarm:swarm orchestrator_api.py langgraph_pipeline.py pytest.ini requirements.txt ./

# Copy pre-built frontend assets (vite outputs to /build/backend/UI/Web/)
COPY --from=frontend-builder --chown=swarm:swarm /build/backend/UI/Web/ backend/UI/Web/

# Ensure runtime dirs exist but are empty in image
RUN mkdir -p var/logs var/artifacts && \
    chown -R swarm:swarm var/

USER swarm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["backend.App.shared.infrastructure.rest.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
