# Free-threaded Python 3.14 (PEP 703, no GIL).
# Офіційного python:3.14t-* image нема — uv ставить freethreaded build
# через python-build-standalone. Multistage: build-essential лишається тільки
# у builder, runtime отримує чистий venv + інтерпретатор.

FROM debian:trixie-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /uvx /bin/

# orjson 3.11.x офіційно дозволяє build на free-threaded Python тільки за
# explicit opt-in (PEP 703 ще experimental у 3.14). Ми свідомо це вмикаємо.
ENV UV_PYTHON_PREFERENCE=only-managed \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=3.14t \
    ORJSON_BUILD_FREETHREADED=1

RUN uv python install 3.14t

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project


FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libssl3 tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local/share/uv/python /root/.local/share/uv/python
COPY --from=builder /app/.venv /app/.venv

ENV PYTHONUNBUFFERED=1 \
    PYTHON_GIL=0 \
    PATH=/app/.venv/bin:$PATH

# Runtime cwd = workspace (host repo mounts here); Codex CLI sidecar
# uses the same path so both processes see one source of truth.
WORKDIR /home/codex/workspace
COPY alembic.ini ./
COPY gunicorn.conf.py ./
COPY migrations/ ./migrations/
COPY app/ ./app/
COPY docker/server/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
