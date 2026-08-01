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
    PYTHONPATH=/app/deps

COPY --from=builder /build/deps /app/deps
COPY . .

EXPOSE 8000

# Same image is used for the API and (in a later task) the worker,
# with the worker service overriding this CMD in Railway/compose.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
