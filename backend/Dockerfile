# --- Build stage ---
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --target=/build/deps .

# --- Runtime stage ---
FROM python:3.11-slim AS runtime

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/deps

COPY --from=builder /build/deps /opt/deps
COPY . .

EXPOSE 8000

# Deployment-readiness audit: Render (and any platform following the
# same convention, e.g. the Railway option originally researched and
# since ruled out) injects the port to bind via the $PORT environment
# variable at runtime — it is not guaranteed to be 8000. The shell
# form (not the JSON exec-array form) is required here so $PORT is
# actually expanded; ${PORT:-8000} falls back to 8000 for any
# environment that doesn't set $PORT (e.g. `docker run` directly,
# docker-compose's local-dev usage below), so this is not a breaking
# change for local development.
CMD python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
