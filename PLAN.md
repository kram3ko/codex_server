# Plan

## Stack (зафіксовано)

**Backend** — Python 3.14, FastAPI 0.136 на ASGI/uvicorn[standard].
**RPC** — Connect-RPC (`connectrpc` 0.10 beta) поверх FastAPI mount на `/api`.
**Streaming** — нативний WebSocket `/chat/ws` (bidi, interrupt). gRPC native не підіймаємо — у браузер не йде без proxy.
**DB** — Postgres 18 (alpine у docker-compose), SQLAlchemy 2.0 async + asyncpg, Alembic для міграцій.
**Object storage** — MinIO (alpine, S3-compat) у docker-compose.
**Codex** — окремий sidecar контейнер з бінарником `codex` (OpenAI CLI 0.128, Rust musl), exposed `ws://codex-app-server:4500` тільки в docker-network. Python + ripgrep + jq у контейнері — щоб модель мала повноцінний tooling у workspace.
**Auth** — JWT (HS256, single-user). `WEB_API_TOKEN` обмінюється на access_token через `AuthService.Login`.
**Frontend** — **Svelte 5 + Vite + TypeScript + Tailwind 4 + connect-es**. Build кладеться у `app/static/dist/`, FastAPI віддає як `StaticFiles`.
**Telegram bot** — `python-telegram-bot` v22 polling всередині FastAPI lifespan, whitelist по user_id.

## Архітектурні правила

- Schemas — `protos/codex/v1/*.proto` (єдине джерело правди). Stubs у `app/grpc_generated/` (gitignored, регенерація через `./scripts/gen-proto.sh`).
- Один RPC-сервіс / WS-роут / endpoint = один файл. Тонкі handlers + товсті services (класи).
- Дев і прод за одним docker-compose. Без env-overrides у docker-compose: `.env` має localhost-адреси для local uvicorn, `environment:` у compose оверайдить на docker-DNS.
- Жодних REST CRUD — все через Connect (uniform protocol). Service-utility `/health` залишається як простий HTTP для docker healthcheck.

## Фази

### A. Фундамент — **готово** ✓

- БД моделі: Conversation, Message, Upload, Note (з tsvector + GIN)
- Engine + sessionmaker, alembic ініт + перша міграція (autogen)
- AuthService class + JWT + FastAPI deps (HTTP/WS)
- Codex sidecar (всі native tools enabled, workspace mount, python/grep/ripgrep)
- Docker-compose: codex-server / codex-app-server / postgres / minio / minio-init
- Proto IDL для Auth + Health, Connect stubs згенеровано
- WS skeleton `/chat/ws` (echo)
- `/health` працює, всі контейнери стартують

### B. Auth + Health через Connect — **наступне**

- `app/rpc/auth.py` — Connect handler для `AuthService.Login`
- `app/rpc/health.py` — Connect handler для `HealthService.Check`
- mount Connect ASGI у `app/main.py` на `/api`
- Smoke: `curl -X POST :8088/api/codex.v1.AuthService/Login -d '{"token":"..."}'` → отримати JWT

### C. Codex client + чат

- `app/services/codex_client.py` — клас `CodexClient` (порт з `aism-py/services/chat/codex/app_server_client.py`), очистка від AISM-специфіки
- WS `/chat/ws` → `CodexClient.run_turn(user_message, attachments)` → стрім токенів у frame'и

### D. CRUD сервіси через Connect

- `protos/codex/v1/conversations.proto` — List, Get, Reset
- `protos/codex/v1/notes.proto` — Save, Search (full-text по tsvector)
- `protos/codex/v1/uploads.proto` — Upload (client-streaming для chunked файлів)
- handlers + services + integration з S3 (`app/services/storage.py`)

### E. Інлайн-tools для агента

- `web_search` — Tavily/Brave ABO native OpenAI web_search (чекаємо T5-перевірку)
- `fetch_url`, `read_file`, `notes_save`, `notes_search` — у `app/tools/`
- Dispatcher у `CodexClient.handle_tool_call`

### F. Frontend (Svelte 5 + Vite + TS + Tailwind 4)

- `web/` директорія: `package.json`, `vite.config.ts`, `tsconfig.json`
- Connect-ES generation з `protos/` через `buf` (або `protoc-gen-es`) → `web/src/gen/`
- Routes: `/login` (введення WEB_API_TOKEN, отримати JWT), `/chat` (WS-стрім, відображення турни, файли)
- `npm run build` → `app/static/dist/`, FastAPI mount

### G. Telegram bot

- `app/tg/bot.py` — клас `TGBotService`, polling у lifespan
- Whitelist `TG_ALLOWED_USER_ID`, file/photo handlers → upload service, текст → CodexClient
- Live-edit повідомлень з throttle 1 edit/sec

### H. Polish

- Smoke pytest (4 тести)
- structlog setup
- pre-commit з ruff
- Деплой: AWS Lightsail / Render

## Стан

- Гілки: `main` (1 commit, ініт), `develop` (1 commit bootstrap, ~30 файлів зараз не закомічено).
- Порт хоста: `8088` (бо 8000-8002 зайняті SalesDep'ом).
- Codex sidecar чекає валідний `OPENAI_API_KEY` — без нього healthy не стане.
