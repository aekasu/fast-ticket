# The API image. Stages: base (locked runtime dependencies + the app, run as a non-root user),
# test (adds the dev extra and the tests: docker-compose.test.yml), runtime (the default target).

FROM python:3.12-slim AS base

# Pinned, so a build today and a build next month use the same uv.
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /usr/local/bin/

# A fixed, unprivileged user: the API needs to read its code and open sockets, nothing else.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Install exactly what uv.lock pins (better layer caching). Without the lockfile the
# image resolved the newest release of every dependency at build time, so it drifted
# from the local venv and CI — e.g. sqlmodel 0.0.47 rejected naive datetimes that
# 0.0.42 accepted, and only the container broke.
COPY pyproject.toml uv.lock ./
ENV UV_FROZEN=1
RUN uv sync --no-dev

COPY app/ ./app/

ENV PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# ── test: the suite on exactly the locked versions (pytest is in the `dev` extra) ─────────────
FROM base AS test
RUN uv sync --extra dev
COPY tests/ ./tests/
USER app
CMD ["pytest", "tests/", "-v", "--tb=short"]

# ── runtime (default) ──────────────────────────────────────────────────────────────────────────
# No --reload here: development adds it in docker-compose.override.yml.
FROM base AS runtime
USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-max-size", "65536"]
