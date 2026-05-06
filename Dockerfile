FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps from lockfile straight into system Python (no .venv created).
# UV_PROJECT_ENVIRONMENT points uv at the system prefix, so `uv sync` reuses
# it instead of provisioning a virtualenv.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY app/ ./app/
COPY docker/server/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
