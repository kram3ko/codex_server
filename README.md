# codex_server

Personal AI playground: FastAPI backend з Codex CLI app-server, Telegram bot
із markdown-рендером + voice→voice, Connect-RPC API і web-chat WebSocket'ом.

## Компоненти

1. **Codex backend** — FastAPI на free-threaded Python 3.14 (no-GIL).
   JSON-RPC 2.0 поверх WS до Codex CLI sidecar (port 4500). Connect-RPC API
   на `/api` для CRUD: `Auth`, `Health`, `User`, `Chat`, `Message`, `Event`,
   `Notes`, `Uploads`. Bidi token streaming через нативний WebSocket
   `/chat/ws`. MCP sub-app на `/mcp/streamable` з bearer-auth.
2. **Telegram bot** — `aiogram` v3 polling всередині FastAPI lifespan.
   Доступний всім TG-юзерам; admin-роль (shell + file_change tools, `/restart`)
   видається через `TG_ADMIN_USER_IDS`. Markdown від Codex рендериться у
   TG-HTML (bold, italic, code, blockquote, lists, links, fences з
   syntax-highlight). Voice in → STT (Speechmatics) → Codex → TTS (Google
   Cloud) → `bot.send_voice`. Multi-bubble streaming з throttle, inline
   controls (Stop / Steer / New thread), native image generation через
   Codex CLI v2 `ThreadItem` + MCP `show_image` для re-delivery без regen.
3. **Web chat** — Svelte 5 + Vite + Tailwind 4 + connect-es. (TODO — фаза F,
   див. `estimates/INTEGRATION.md`.)

## Сховища

- **Postgres 18** — `users` (з `role` enum), `chats`, `messages`, `events`,
  `uploads`, `notes` (full-text search через `tsvector` + GIN). Локально —
  у docker-compose. Cloud-prod (Neon) — backlog.
- **Redis 8.6** — pub/sub bus для ChatEvent fan-out (TG renderer + web WS +
  monitoring), polling-lock для безпечного multi-instance restart'у.
- **S3-compatible object storage** для media (image_generation outputs, TG
  uploads). Локально — MinIO. Cloud-prod (Cloudflare R2) — backlog: той самий
  `aiobotocore`-клієнт, міняється тільки endpoint.

## Запуск

```bash
cp .env.example .env             # заповнити: TG_BOT_TOKEN, TG_ADMIN_USER_IDS,
                                 # REDIS_PASSWORD, SPEECHMATICS_API_KEY,
                                 # GOOGLE_TTS_API_KEY, MCP_CALLBACK_TOKEN
docker compose up -d             # codex-server + codex-app-server +
                                 # codex-app-server-guest + postgres + redis + minio
```

Перед першим запуском гостьового sidecar — авторизуватись окремо (alt-ChatGPT
аккаунт, не admin):

```bash
docker compose run --rm codex-app-server-guest codex login --device-auth
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
   TG_ADMIN_USER_IDS=123456789,987654321   # comma-separated; promote → ADMIN
   ```

Юзери поза `TG_ADMIN_USER_IDS` отримують `UserRole.USER` і ходять у
**окремий гостьовий Codex-контейнер** (без host-repo mount, без
`docker.sock`, окремий ChatGPT login → ізольований token-budget). Admin
користується основним sidecar'ом з повним доступом.

Команди:
- `/new` — новий thread (скинути контекст моделі, лишити DB history)
- `/stop` — перервати поточну відповідь (`turn/interrupt`)
- `/reset` — закрити сесію + очистити stored thread_id
- `/restart` — ребут контейнера через Docker socket (admin-only)

Voice/audio транскрибуються Speechmatics (`SPEECHMATICS_API_KEY`). Текстову
відповідь Codex'а проганяємо через Google Cloud TTS (`GOOGLE_TTS_API_KEY`)
у OGG_OPUS і шлемо як `bot.send_voice`. Якщо `GOOGLE_TTS_API_KEY` порожній —
voice-input отримує текстову відповідь.

## Markdown у TG

Codex повертає markdown (`**bold**`, fences, lists, blockquote). Сервіс
`tg_markdown` (`app/tg/markdown.py`) рендерить у TG-HTML whitelisted-тегами:

| Markdown            | TG render                                  |
|---------------------|--------------------------------------------|
| `# Header`          | `<b>Header</b>`                            |
| `**bold**` `*it*`   | `<b>bold</b>` `<i>it</i>`                  |
| `` `code` ``        | `<code>code</code>` (моноширний)           |
| ` ```py x=1 ``` `   | `<pre><code class="language-py">x=1</code></pre>` |
| `- a` / `1. a`      | `• a` / `1. a` (з indent для nested)       |
| `> quote`           | `<blockquote>quote</blockquote>`           |
| `[txt](url)`        | `<a href="url">txt</a>` (клікабельне)      |
| `~~strike~~`        | `<s>strike</s>`                            |
| `<table>`, `<h1>`...| дроп (інакше TG → 400)                     |

Chunks ≤4096 з balanced `<pre>` (cut усередині fence додає `</code></pre>` +
`<pre><code>` у наступному). Voice mode шле через `tg_markdown.to_plain` —
Google озвучує без зірочок/backtick'ів.

## Workflow

- **Codex CLI persona** живе у `AGENTS.md` — auto-load'иться сидеком на
  кожному турні. Контракт там: edit'и файлів — спитати "ок"; shell/git —
  робити одразу; image_generation → save у `~/.codex/generated_images/`,
  caption мовою юзера, не дублювати internal prompt-поля у відповіді.
- **Гостьовий персонаж** — `docker/codex/AGENTS-guest.md` (mounted у гостьовий
  контейнер як `/home/codex/workspace/AGENTS.md:ro`); мінімальна persona без
  repo/git/code-style.
- **Міграції:** `alembic upgrade head` (auto'ом на entrypoint'і, з
  `timeout 30` щоб asyncpg-teardown не вішав boot). Head: `8a3e1c5d4f02`
  (User.role enum).
- **Self-restart:** `/restart` у боті → `DockerControlService.restart_container`
  через `/var/run/docker.sock` (без `docker` CLI в образі). Admin-only.
- **Тести:** `uv run --group test pytest -q` (37 тестів).
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
│   ├── mcp/                    MCP sub-app (/mcp/streamable, bearer auth)
│   │   ├── core.py             FastAPI sub-app + auth middleware
│   │   ├── errors.py
│   │   └── tools/              show_image (re-deliver image without regen)
│   ├── rpc/                    Connect-RPC handlers
│   │   ├── auth.py, health.py, user.py, chat.py, message.py, event.py
│   │   ├── notes.py, uploads.py
│   │   ├── _mappers.py         ORM ↔ pb conversions (single source of truth)
│   │   └── router.py           ConnectRouter ASGI mount
│   ├── ws/chat.py              /chat/ws bidi-token-стрім
│   ├── tg/                     aiogram polling
│   │   ├── service.py          bot lifecycle
│   │   ├── handlers.py         /new /stop /reset /restart + callbacks
│   │   ├── sessions.py         per-chat CodexClient store + admin/guest routing
│   │   ├── turn.py             TurnRunner (persist + stream + emit events)
│   │   ├── progress.py         status bubble + multi-bubble streaming
│   │   ├── output.py           send_text / send_voice_reply / send_attachment
│   │   ├── media.py            STT + attachment pipeline для inbound
│   │   └── markdown.py         TelegramMarkdown (singleton tg_markdown)
│   ├── models/                 SQLAlchemy ORM (User, Chat, Message, Event,
│   │                           Upload, Note + StrEnum kinds; UserRole)
│   ├── services/               <resource>/{service.py, default.py, __init__.py}
│   │   ├── auth/               JWT issue/verify
│   │   ├── codex/              CodexClient + transport (JSON-RPC over WS)
│   │   │                       + events.py (typed pydantic ChatEvent +
│   │   │                       ToolCallRecord TypedDict)
│   │   ├── chats/, users/, messages/, events/, uploads/, notes/
│   │   ├── bus/                Redis pub/sub fan-out
│   │   ├── cache/              Redis client singleton
│   │   ├── storage/            S3/MinIO via aiobotocore
│   │   ├── stt/                Speechmatics batch v2
│   │   ├── tts/                Google Cloud TTS (REST v1, OGG_OPUS)
│   │   └── docker/             host docker daemon control (/restart)
│   ├── utils/paths.py          trusted-root resolver для tool-supplied paths
│   ├── deps/                   FastAPI auth dep
│   └── grpc_generated/         Connect stubs (gitignored regen via scripts/gen-proto.sh)
├── protos/codex/v1/            *.proto (auth, chat, common, event, message,
│                               user, notes, uploads)
├── migrations/versions/        alembic (init_schema, add_user_role)
├── tests/                      pytest (chat_session, chat_ws, codex_client,
│                               messages_service, tg_markdown, tg_progress,
│                               tg_turn, user_service)
├── docker/codex/               Dockerfile + entrypoint + AGENTS-guest.md
├── docker/server/              entrypoint.sh
├── docker-compose.yml          codex-server + codex-app-server +
│                               codex-app-server-guest + postgres + redis + minio
├── pyproject.toml              Python 3.14 + deps
├── AGENTS.md                   Codex CLI persona contract (admin)
├── estimates/INTEGRATION.md    phase-by-phase status
└── README.md                   ← цей файл
```
