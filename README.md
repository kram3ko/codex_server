# codex_server

Personal AI playground: FastAPI backend з Codex CLI app-server, Telegram bot
(одиничний whitelisted user) і web-чатом зі стрімінгом.

## Компоненти

1. **Codex backend** — FastAPI, SSE-стрім токенів, інтеграція з Codex CLI
   app-server, інлайн-tools (`web_search`, `fetch_url`, `read_file`,
   `analyze_image`, `notes_save`, `notes_search`).
2. **Telegram bot** — `python-telegram-bot` v22+, polling всередині FastAPI
   lifespan, live-edit повідомлень з прогресом стріму.
3. **Web chat** — 1 HTML-файл (Vue 3 + Tailwind через CDN), SSE через
   `EventSource`, markdown + highlight.js.

## Сховища

- **Postgres** — `conversations`, `messages`, `uploads`, `notes`
  (full-text search через `tsvector` + GIN). Локально — у docker-compose,
  prod — Neon.
- **S3-compatible object storage** для файлів. Локально — MinIO у
  docker-compose, prod — Cloudflare R2 (той самий `aiobotocore`-клієнт,
  міняється тільки endpoint).

## Локальний запуск (план T14)

```bash
cp .env.example .env             # заповнити токени
docker-compose up                # FastAPI + Postgres + MinIO
# або без docker:
uv sync && uvicorn app.main:app --reload
```

## Структура

```
codex_server/
├── app/
│   ├── main.py              FastAPI() + lifespan (TG bot startup)
│   ├── config.py            pydantic-settings (читає .env)
│   ├── db/                  [todo T3] AsyncEngine + sessionmaker
│   ├── models/              [todo T3] SQLAlchemy ORM
│   │                         conversation / message / upload / note
│   ├── routers/             [todo T6,T7] HTTP-роутери
│   │                         /chat (SSE) /upload /history /reset
│   ├── services/            [todo T4,T5] codex_client, storage (S3)
│   ├── tools/               [todo T8-T11] агентські інструменти
│   │                         web_search / fetch_url / read_file / notes
│   ├── tg/                  [todo T12] python-telegram-bot Application
│   └── static/              [todo T13] index.html (web chat)
├── migrations/              [todo T3] alembic
├── pyproject.toml           Python 3.14 + deps (fastapi, ptb, ...)
├── uv.lock                  pinned deps
├── .env.example             шаблон ENV
├── .gitignore / .dockerignore
├── Dockerfile               [todo T14]
├── docker-compose.yml       [todo T14] FastAPI + Postgres + MinIO
└── README.md
```
