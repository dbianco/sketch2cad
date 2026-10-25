# syntax=docker/dockerfile:1

# Image shared by the `api` and `worker` compose services. No secrets are baked
# in: DATABASE_URL, SESSION_SECRET, SIGNING_KEY_PATH and APP_ENV come from the
# runtime environment.

# Build stage: resolve the locked runtime dependencies into /app/.venv.
FROM python:3.12.13-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, so this layer is cached while only the sources change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Runtime stage: the virtual environment plus migrations, without uv.
FROM python:3.12.13-slim

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --uid 10001 --user-group --no-create-home --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=build /app/.venv ./.venv
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

USER app

EXPOSE 8000

# Default command (the `api` service); compose overrides it to run migrations
# first, and the `worker` service runs the Procrastinate worker instead.
CMD ["uvicorn", "--factory", "sketch2cad.api:create_app", "--host", "0.0.0.0", "--port", "8000"]
