# codex_server

Personal AI playground: FastAPI backend з Codex CLI app-server, Telegram bot
(одиничний whitelisted user) і web-чатом зі стрімінгом через WebSocket.

## Компоненти

1. **Codex backend** — FastAPI на free-threaded Python 3.14 (no-GIL).
   JSON-RPC 2.0 поверх WS до Codex CLI sidecar (port 4500). Connect-RPC API
   на `/api` для CRUD (Auth/Health/User/Chat/Message/Event). Bidi token
   streaming через нативний WebSocket `/chat/ws`.
2. **Telegram bot** — `aiogram` v3 polling всередині FastAPI lifespan,
   whitelist по `TG_ALLOWED_USER_IDS`, multi-bubble streaming довгих
   відповідей, inline-controls (Stop / Steer / New thread), STT через
   Speechmatics, native image generation через Codex CLI v2 ThreadItem.
3. **Web chat** — Svelte 5 + Vite + Tailwind 4 + connect-es. (TODO — ще не
   почато, див. `PLAN.md` фаза F).

## Сховища

- **Postgres 18** — `users`, `chats`, `messages`, `events`, `uploads`,
  `notes` (full-text search через `tsvector` + GIN). Локально — у
  docker-compose. Cloud-prod (Neon) — backlog.
- **Redis 8.6** — pub/sub bus для ChatEvent fan-out (TG renderer + web WS +
  monitoring), polling-lock для безпечного multi-instance restart'у.
- **S3-compatible object storage** для media (image_generation outputs тощо).
  Локально — MinIO. Cloud-prod (Cloudflare R2) — backlog: той самий
  `aiobotocore`-клієнт, міняється тільки endpoint.

## Запуск

```bash
cp .env.example .env             # заповнити: TG_BOT_TOKEN, TG_ALLOWED_USER_IDS,
                                 # OPENAI_API_KEY, REDIS_PASSWORD, SPEECHMATICS_API_KEY
docker compose up -d             # codex-server + codex-app-server + postgres + redis + minio
```

Без docker (тільки FastAPI, без Codex sidecar):

```bash
uv sync && uvicorn app.main:app --port 8088
```

Порт хоста — `8088` (8000-8002 зайняті іншим). Усередині контейнера — `8000`.

## Telegram bot

1. Створити bot token через `@BotFather`.
2. Дізнатися Telegram user id (`@userinfobot`).
3. У `.env`:
   ```bash
   TG_BOT_TOKEN=...
   TG_ALLOWED_USER_IDS=123456789      # один id або JSON-list [123,456]
   ```

Команди:
- `/new` — новий thread (скинути контекст моделі, лишити DB history)
- `/stop` — перервати поточну відповідь (`turn/interrupt`)
- `/reset` — закрити сесію + очистити stored thread_id
- `/restart` — ребут контейнера через Docker socket

Voice/audio транскрибуються Speechmatics (`SPEECHMATICS_API_KEY`) і йдуть у
Codex як текст. `OPENAI_API_KEY` для STT не потрібен.

## Workflow

- **Codex CLI persona** живе у `AGENTS.md` — auto-load'иться сидеком на
  кожному турні. Контракт там: edit'и файлів — спитати "ок"; shell/git —
  робити одразу; не дублювати internal image-prompt поля у відповіді.
- **Міграції:** `alembic upgrade head` (auto'ом на entrypoint'і, з
  `timeout 30` щоб asyncpg-teardown не вішав boot).
- **Self-restart:** `/restart` у боті → `DockerControlService.restart_container`
  через `/var/run/docker.sock` (без `docker` CLI в образі).
- **Тести:** `uv run --group test pytest -q` (зараз 20 тестів).
- **Lint/types:** `uv run --group lint ruff check app/ tests/` + `pyright app/`.
- **Гарячий редеплой коду:** edit → save → `docker compose restart codex-server`
  (workspace mount = source of truth, alembic зробить pending міграції).

## Структура

```
codex_server/
├── app/
│   ├── main.py                 FastAPI() + lifespan (TG polling, Codex sidecar)
│   ├── config.py               pydantic-settings
│   ├── api/                    HTTP endpoints (health, chat_ws + OpenAPI docs)
│   ├── rpc/                    Connect-RPC handlers (auth/health/user/chat/message/event)
│   ├── ws/chat.py              /chat/ws bidi-token-стрім
│   ├── tg/                     aiogram polling, sessions store, turn runner,
│   │                           progress bar, multi-bubble streaming, output
│   ├── models/                 SQLAlchemy ORM (User, Chat, Message, Event,
│   │                           Upload, Note + StrEnum kinds)
│   ├── services/               <resource>/{service.py, default.py, __init__.py}
│   │   ├── auth/               JWT issue/verify
│   │   ├── codex/              CodexClient + transport (JSON-RPC over WS)
│   │   │                       + events.py (typed pydantic ChatEvent)
│   │   ├── chats/, users/, messages/, events/, uploads/, notes/
│   │   ├── bus/                Redis pub/sub fan-out
│   │   ├── cache/              Redis client singleton
│   │   ├── storage/            S3/MinIO via aiobotocore
│   │   ├── stt/                Speechmatics batch v2
│   │   ├── tts/                placeholder
│   │   └── docker/             host docker daemon control (/restart)
│   ├── deps/                   FastAPI auth dep
│   └── grpc_generated/         Connect stubs (gitignored regen via scripts/gen-proto.sh)
├── protos/codex/v1/            *.proto (auth, chat, common, event, message, user)
├── migrations/versions/        alembic
├── tests/                      pytest (chat_session, chat_ws, codex_client,
│                               messages_service, tg_progress, tg_turn)
├── docker/codex/, docker/server/   Dockerfile + entrypoint
├── docker-compose.yml          codex-server + codex-app-server + postgres + redis + minio
├── pyproject.toml              Python 3.14 + deps (orjson, pydantic, aiogram, ...)
├── AGENTS.md                   Codex CLI persona contract
├── PLAN.md                     phase-by-phase status
└── README.md                   ← цей файл
```
