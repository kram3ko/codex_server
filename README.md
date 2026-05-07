# codex_server

Personal AI playground: FastAPI backend з Codex CLI app-server, Telegram bot
(одиничний whitelisted user) і web-чатом зі стрімінгом.

## Компоненти

1. **Codex backend** — FastAPI, SSE-стрім токенів, інтеграція з Codex CLI
   app-server, інлайн-tools (`web_search`, `fetch_url`, `read_file`,
   `analyze_image`, `notes_save`, `notes_search`).
2. **Telegram bot** — `aiogram` v3, polling всередині FastAPI
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
uv sync && uvicorn app.main:app
```

## Telegram bot

1. Створи bot token через `@BotFather`.
2. Дізнайся свій numeric Telegram user id через `@userinfobot` або аналог.
3. У `.env` заповни:

```bash
TG_BOT_TOKEN=...
TG_ALLOWED_USER_IDS=123456789
```

У docker-compose FastAPI стартує polling автоматично. Пиши боту звичайний текст,
надсилай фото або voice/audio - voice буде транскрибований і піде в Codex як текст.
Для voice/audio потрібен `SPEECHMATICS_API_KEY`; `OPENAI_API_KEY` до транскрипції
не використовується.
`/reset` закриває поточну Codex-сесію чату і відкриває нову на наступному повідомленні.

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
│   ├── tg/                  Telegram polling, attachments, progress UI
│   ├── tools/               [todo T8-T11] агентські інструменти
│   │                         web_search / fetch_url / read_file / notes
│   └── static/              [todo T13] index.html (web chat)
├── migrations/              [todo T3] alembic
├── pyproject.toml           Python 3.14 + deps (fastapi, aiogram, ...)
├── uv.lock                  pinned deps
├── .env.example             шаблон ENV
├── .gitignore / .dockerignore
├── Dockerfile               [todo T14]
├── docker-compose.yml       [todo T14] FastAPI + Postgres + MinIO
└── README.md
```
