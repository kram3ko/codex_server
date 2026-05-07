# Plan

## Stack (зафіксовано)

- **Backend** — Python 3.14 free-threaded (no-GIL), FastAPI 0.136 на uvicorn[standard].
- **RPC** — Connect-RPC (`connectrpc` 0.10) поверх FastAPI mount на `/api`.
- **Streaming** — нативний WebSocket `/chat/ws` (bidi, interrupt). gRPC native не підіймаємо — у браузер не йде без proxy.
- **Bus** — Redis 8.6 pub/sub (`chat:{chat_id}:events`) для fan-out ChatEvent'ів між TG renderer / web WS / monitoring.
- **DB** — Postgres 18 (alpine), SQLAlchemy 2.0 async + asyncpg, Alembic.
- **Object storage** — MinIO локально, Cloudflare R2 для cloud-deploy (той самий `aiobotocore`, тільки endpoint).
- **Codex sidecar** — `codex` CLI 0.128 (Rust musl), exposed `ws://codex-app-server:4500` тільки в docker-network. Native tools enabled (web_search, image_generation, shell, file_change тощо).
- **Auth** — JWT (HS256, single-user). `WEB_API_TOKEN` обмінюється на access_token через `AuthService.Login`.
- **Frontend** — Svelte 5 + Vite + TS + Tailwind 4 + connect-es (TODO, фаза F).
- **Telegram bot** — `aiogram` 3.27 polling всередині FastAPI lifespan, whitelist по `TG_ALLOWED_USER_IDS`.
- **STT** — Speechmatics batch v2 (`language='auto'`).
- **Serialization** — orjson hot-path (events / bus / transport / WS), opt-in build для cpython-3.14t через `ORJSON_BUILD_FREETHREADED=1`.

## Архітектурні правила

- Schemas — `protos/codex/v1/*.proto` (єдине джерело правди). Stubs у `app/grpc_generated/` (gitignored, регенерація через `./scripts/gen-proto.sh`).
- Один RPC-сервіс / WS-роут / endpoint = один файл. Тонкі handlers + товсті services (класи).
- Один docker-compose для всього. Без env-overrides у compose: `.env` має localhost-адреси для local uvicorn, `environment:` у compose оверайдить на docker-DNS.
- Жодних REST CRUD — все через Connect (uniform protocol). `/health` залишається як простий HTTP для docker healthcheck.
- ChatEvent шлеться структуровано (typed pydantic + orjson), не markdown bridge.
- Codex item-types — source of truth `openai/codex` repo `v2/item.rs`; нові variants додавати у `_BUILTINS` declarative spec.

## Фази

### A. Фундамент — ✓ done

DB моделі (User/Chat/Message/Event/Upload/Note + StrEnum), AsyncEngine, alembic init + перша міграція, Codex sidecar, docker-compose (codex-server / codex-app-server / postgres / redis / minio + minio-init), proto IDL, WS skeleton.

### B. Auth + Health Connect — ✓ done

`app/rpc/auth.py`, `app/rpc/health.py`, mount у `/api`. JWT issue/verify через `AuthService.Login`.

### C. Codex client + chat WS — ✓ done

`app/services/codex/client.py` — `CodexClient` з повним покриттям Codex CLI v2 ThreadItem (16 типів через `_BUILTINS` declarative spec). `app/ws/chat.py` — bidi WS з token streaming, dedup `_final_text_for_done_frame`. Typed `Attachment` flow для image_generation/imageView (без markdown round-trip).

### D. CRUD Connect — частково done

✓ `protos/codex/v1/{auth,chat,common,event,message,user}.proto`
✓ `app/rpc/{auth,chat,event,health,message,user}.py` + service shims
✗ `protos/codex/v1/uploads.proto` — Upload (client-streaming для chunked файлів)
✗ `protos/codex/v1/notes.proto` — Save/Search через tsvector

### E. Inline tools — ⨯ skipped

Codex CLI sidecar 0.128 уже має нативно: `web_search`, `image_generation`, `image_view`, `mcp_tool_call`, `command_execution`, `file_change`, `dynamic_tool_call`. Свої тулзи у `app/tools/` не потрібні — sidecar повністю покриває use-case.

### F. Frontend (Svelte 5) — TODO

- `web/` директорія: `package.json`, `vite.config.ts`, `tsconfig.json`
- Connect-ES generation з `protos/` через `buf` → `web/src/gen/`
- Routes: `/login` (введення `WEB_API_TOKEN` → JWT), `/chat` (WS-стрім, рендер турни, файли)
- `npm run build` → `app/static/dist/`, FastAPI mount

### G. Telegram bot — ✓ done + extras

- `app/tg/service.py` — `TGBotService` polling у lifespan, whitelist
- `app/tg/turn.py` — `TurnRunner` з turn_lock, asyncio.timeout(300s), Codex steer/interrupt, history seed на fresh thread (10 last messages → `thread/inject_items`)
- `app/tg/sessions.py` — `ChatSessionStore` з Future-per-chat lazy init, atomic `consume_steer`
- `app/tg/progress.py` — TurnProgressReporter, multi-bubble streaming (chunks ≥180 char, 1.2s throttle), inline-controls (Stop / Steer / New thread)
- `app/tg/output.py` — `send_text` + `send_attachment` (typed)
- `app/tg/media.py` — Speechmatics voice→text, photo/document download
- Polling-lock у Redis (TTL 60s, renew /3) для multi-instance safety
- `/restart` команда через `DockerControlService` (httpx + UDS до `/var/run/docker.sock`)

### H. Polish — частково done

✓ pytest (20 тестів: chat_session, chat_ws, codex_client, messages_service, tg_progress, tg_turn)
✓ structlog глобально
✓ ruff + pyright clean
✗ pre-commit hooks
✗ Cloud deploy (див. нижче)

### I. Cloud deploy — backlog

- Host: AWS Lightsail / Render / Fly.io / Hetzner Cloud — TBD за ціною
- Postgres → Neon (managed, free tier має 1 project + branch)
- Object storage → Cloudflare R2 (S3-compat, 10 GB free, без egress fees)
- TG bot polling → лишається polling (не webhook — щоб не світити публічний URL)
- Secrets — env vars через cloud провайдера (без `.env` файла на проді)
- HTTPS — Cloudflare proxy перед сервером (TLS termination + DDoS shield)
- Backups — Postgres → щоденний `pg_dump` у R2; MinIO/R2 — versioning enabled

## Done above plan

Речі що додалися поза phase'ами (з'явилися під час роботи):

- **Multi-bubble TG streaming** — chunks ≥180 char + 1.2s throttle, `committed_text` для дедуп з final_text у `_strip_committed_prefix`
- **Inline turn controls** — Stop / Steer / New thread як `InlineKeyboardMarkup`
- **STT через Speechmatics** — voice/audio messages транскрибуються і йдуть у Codex як текст
- **MinIO upload pipeline** — `image_generation` artifacts автоматично копіюються у S3, `uploads` row з MIME/size
- **Redis bus** — pub/sub `chat:{chat_id}:events` для fan-out ChatEvent'ів (TG + web WS + future monitoring)
- **Polling-lock** — Redis-based leader election для TG polling (захист від `Conflict: terminated by other getUpdates`)
- **Self-restart** — `/restart` команда через Docker daemon API (`/var/run/docker.sock`)
- **Pydantic v2 + orjson** — events.py через BaseModel + Field(description=...) → JSON Schema готова, серіалізація через orjson bytes-native
- **Free-threaded Python 3.14** — no-GIL з opt-in build orjson через `ORJSON_BUILD_FREETHREADED=1`
- **AGENTS.md persona** — Codex CLI auto-load'ить контракт: edit → ask, shell → do, no internal-prompt-echo, watercolor default для images
- **Codex CLI v2 ThreadItem coverage** — 16 типів через declarative `_BUILTINS` spec (раніше було 8, KeyError на `imageGeneration`)
- **Stale thread retry** — `_begin_turn_with_retry` для recovery після sidecar restart (-32600 thread not found → invalidate + retry once)

## Стан (поточний)

- Гілки: `develop` (live, 4 локальних коміти попереду origin), `main` (init).
- Хост порт `8088`; внутрішній `8000`. Sidecar `codex-app-server` на `4500` (тільки docker-network).
- Бот живий: `@SkyroAIBot`, whitelist `470270260`.
- Codex sidecar потребує `OPENAI_API_KEY`; без нього healthcheck не пройде.
