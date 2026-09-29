FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install exactly what uv.lock pins (better layer caching). Without the lockfile the
# image resolved the newest release of every dependency at build time, so it drifted
# from the local venv and CI — e.g. sqlmodel 0.0.47 rejected naive datetimes that
# 0.0.42 accepted, and only the container broke.
COPY pyproject.toml uv.lock ./
ENV UV_FROZEN=1
RUN uv sync --no-dev

# Copy application source
COPY app/ ./app/
COPY tests/ ./tests/

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
