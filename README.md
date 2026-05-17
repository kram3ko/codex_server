# codex_server

Personal AI playground: FastAPI backend з Codex CLI app-server, Telegram bot
із markdown-рендером + voice↔voice, Connect-RPC API і web-chat на gRPC
server-streaming (HTTP/2) поверх Connect-RPC.

## Компоненти

1. **Codex backend** (`codex-server`) — FastAPI на free-threaded Python 3.14
   (no-GIL). JSON-RPC 2.0 поверх WS до Codex CLI sidecar (port 4500).
   Connect-RPC API на `/api`: `Auth`, `Health`, `User`, `Chat` (server-stream
   `RunTurn` + `TailTurn` + `StreamCodexUsage`, unary `InterruptTurn`/
   `SteerTurn`), `Message`, `Event`, `Notes`, `Uploads`, `Admin`. MCP sub-app
   на `/mcp/streamable` з bearer-auth. **Native WebSocket на browser-edge'і
   вже нема** — Connect-RPC server-streaming через HTTP/2 flow-control window
   забезпечує backpressure натурально. WS лишається тільки до Codex CLI
   sidecar (їх wire-protocol).
2. **TaskIQ worker** (`codex-worker`) — durable виконавець turn-ів.
   `RunTurn` RPC ставить `execute_turn.kiq(turn_id)` через
   `taskiq-redis RedisStreamBroker`; web container лишається тонким, web tail-
   ить per-turn Redis stream `turn:{id}:events`. `--ack-type when_executed` +
   `finalize_once` CAS у Postgres = exactly-once terminal попри
   at-least-once redelivery (виявляється через CAS-active-lock `turn:active:
   {chat_id}` == `str(turn_id)` → silent skip).
3. **Telegram bot** — `aiogram` v3 polling всередині FastAPI lifespan.
   Доступний усім TG-юзерам; admin-роль (shell + file_change tools,
   `/restart`) видається через `TG_ADMIN_USER_IDS`. Markdown від Codex
   рендериться у TG-HTML (bold, italic, code, blockquote, lists, links,
   fences з syntax-highlight). Voice in → STT (Speechmatics) → Codex → TTS
   (Google Cloud) → `bot.send_voice`. Multi-bubble streaming з throttle,
   inline controls (Stop / Steer / New thread), native image generation через
   Codex CLI v2 `ThreadItem` + MCP `show_image` для re-delivery без regen.
4. **Web chat** — Svelte 5 (runes: `$state`/`$derived`/`$effect`) + Vite +
   Tailwind 4 + `@connectrpc/connect-web` + `@bufbuild/protobuf`. Streaming
   turns через `ChatService.runTurn` (server-stream), reconnect через
   `TailTurn(turn_id)` (per-turn primary handle, chat-id legacy fallback),
   typewriter-render, interrupt/steer, voice in (TTS reply теж), image
   attach. Tool-calls (live і historical) — згорнутий «N completed»
   дропдаун (`CompletedTools.svelte`). Codex rate-limit usage прилітає live
   через `StreamCodexUsage` (event-driven: bootstrap snapshot на open +
   pub/sub push після кожного `finalize_once`; manual `RefreshCodexUsage`
   unary RPC для force-fetch кнопки). Sentry frontend
   (`@sentry/svelte`) → self-hosted Bugsink. Live у
   `codex-web` Vite dev контейнері на `http://localhost:8088`.

## Архітектура

```
                     ┌────────────────┐
                     │   Telegram     │
                     │  (long-poll)   │
                     └────────┬───────┘
                              │ HTTPS
                              ▼
   ┌──────────┐ HTTP/2  ┌─────────────────────────────────┐  WS (JSON-RPC 2.0)  ┌──────────────────┐
   │ Browser  │◄───────►│   codex-server (FastAPI 3.14t)  │◄───────────────────►│ codex-cli        │
   │ Svelte 5 │ Connect │  ┌─────────────────────────┐    │                     │ admin sidecar    │
   │ ConnectRPC         │  │ /api  ConnectRouter     │    │  WS (separate)      ├──────────────────┤
   └──────────┘         │  │  Auth/Health/User/Chat  │    │◄───────────────────►│ codex-cli        │
                        │  │  Message/Event/Notes/   │    │                     │ guest sidecar    │
                        │  │  Uploads/Admin          │    │                     └──────────────────┘
                        │  │ /mcp/streamable (bearer)│    │
                        │  │ TG aiogram polling      │    │       enqueue
                        │  │ usage_poller (lifespan) │    │  ┌────────►┐
                        │  └──────────┬──────────────┘    │  │         ▼
                        └─────┬───────┼────────┬──────────┘  │  ┌─────────────────┐
                              │       │        │             │  │ codex-worker    │ TaskIQ
                              ▼       ▼        ▼             │  │ (taskiq-redis   │ ack=
                       ┌──────────┐ ┌──────┐ ┌──────────────┐│  │  RedisStream)   │ when_executed
                       │ Postgres │ │Redis │ │ S3 / MinIO   ││  └────────┬────────┘
                       │  18      │ │ 8.6  │ │ (R2 у проді) ││           │ WS
                       │ turns +  │ │ pub/ │ │              │└───────────┴───────────────┐
                       │ msgs +   │ │ sub  │ └──────────────┘                            ▼
                       │ events   │ │ +    │                                  open_codex_turn
                       │          │ │stream│                                  → admin/guest sidecar
                       └──────────┘ └──────┘

                       Observability:  Sentry SDK → Bugsink (self-hosted, :8089)
                       Auto-restart:   docker/watchdog (label codex.watchdog=true)
```

**Turn lifecycle.** `RunTurn` RPC: створює `turns` row у `STARTING` →
`execute_turn.kiq(turn_id)` → yield `TurnStartedEvent(turn_id)` → tail per-
turn Redis stream `turn:{turn_id}:events`. Worker stage: acquires
`turn:active:{chat_id}` CAS-lock, відкриває Codex sidecar WS, `mark_running`
у CAS-update (STARTING→RUNNING), стрімить події у `turn:{id}:events`,
heartbeat loop оновлює `heartbeat_at` кожні 5s, `finalize_once` CAS пише
terminal status. **Reconnect**: web reconnect-ить через `TailTurn(turn_id)`;
сервер XREAD-ить stream від `after_id`, або повертає synthetic terminal якщо
`turns.status` уже у `(COMPLETED|FAILED|CANCELLED)`.

**Backpressure.** Codex WS notification queue (bounded per-sidecar:
`CODEX_NOTIFICATION_QUEUE_MAX_ADMIN=400`, `_GUEST=1000`, drop-oldest+WARN) →
worker `stream_turn` (token-delta coalesce, `CHAT_TOKEN_COALESCE_BYTES=256`)
→ `XADD turn:{id}:events MAXLEN ~ 5000` → web `XREAD BLOCK 2000` → Connect-
RPC server stream → HTTP/2 flow-control. Stream має TTL `TURN_STREAM_TTL_S=
86400` (24h) для post-finalize tail. Slow client блокує `ASGI send` → блокує
`yield` → не впливає на worker bo той пише в Redis stream незалежно (worker
рухається у швидкості Codex, web — у швидкості клієнта).

**Cross-worker control.** `gunicorn -w 2+` worker'и не шарять state у пам'яті
— `app/services/codex/codex_remote.py` робить one-shot WS до sidecar:
- `send_steer_by_ids(thread_id, codex_turn_id, text, turn_id)` — `turn/steer` +
  `note_steer(chat_id, codex_turn_id)` (idle-watchdog keepalive) +
  `bump_steer_count(turn_id)` (INCR-counter для stream-loop cut-segment
  boundary).
- `send_interrupt_turn_id(is_admin, codex_turn_id)` — `turn/interrupt`.
- `consume_steer(chat_id, codex_turn_id)` — owner-worker idle-handler споживає
  keepalive щоб не вбити turn посеред steer-додатку.
- `peek_steer_count(turn_id)` — stream-loop polling boundary signal.

**Recovery.** Lifespan startup → `reconcile_stale_turns()` фіналізує orphan
turns з протухлим `heartbeat_at`. Event-driven recovery під час roботи:
`reconcile_if_stale(turn)` inline check у `_handle_active_turn`/
`_handle_active_tg_turn` — не периодичний loop, а перевірка коли її дійсно
потрібно.

## Сховища

- **Postgres 18** — `users` (з `role` enum + `password_hash`), `chats`,
  `messages`, `events`, `uploads` (з `user_id` FK), `notes` (full-text
  search через `tsvector` + GIN), `invites` (single-use signup), **`turns`**
  (durable turn lifecycle — `STARTING`/`RUNNING`/`COMPLETED`/`FAILED`/
  `CANCELLED` + partial unique index `turns_active_per_chat WHERE status IN
  ('STARTING','RUNNING')` для race-free pre-lock). Локально — у docker-
  compose. Cloud-prod (Neon) — backlog.
- **Redis 8.6** — кілька різних callsite-груп:
  - **`turn:{turn_id}:events`** — per-turn `XADD MAXLEN ~5000`, TTL 24h.
    Live транспорт ChatEvent від worker → web/TG. `XREAD BLOCK
    TURN_STREAM_TAIL_BLOCK_MS` зі сторони tail-er-а.
  - **`turn:active:{chat_id}`** — CAS-lock з value=`str(turn_id)` + 30s TTL,
    refreshed heartbeat-ом. Worker redelivery distinguish: `value ==
    str(turn_id)` → REDELIVERY → silent skip (no terminal publish).
  - **`codex:steer-count:{turn_id}`** — INCR-counter (boundary signal для
    stream-loop cut_segment). Bumped у `send_steer_by_ids` на accept.
  - **`codex:active-steer:{chat_id}`** — keepalive value=`codex_turn_id` для
    owner-worker idle-watchdog (відрізнити "повна тиша" від "інший воркер
    щойно інжектив steer-text").
  - **`codex:usage:{sidecar}`** — pub/sub channel; `codex:usage:snapshot:
    {sidecar}` cache key (60s TTL) для bootstrap нових підписників.
  - **pub/sub bus `chat:{chat_id}:events`** — legacy fan-out (TG renderer +
    monitoring). Web більше не споживає звідси — `turn:{id}:events` як primary.
  - **polling-lock** для безпечного multi-instance restart'у TG бота.
  - **orphan-thread quarantine** (24h TTL) для broken Codex thread_id.
  - **cache singleton** для transient state.
  - **TaskIQ Redis stream broker** — `RedisStreamBroker` як queue +
    `RedisAsyncResultBackend`. Окреме DB-id за замовчуванням той самий
    `REDIS_URL`.
- **S3-compatible object storage** для media (image_generation outputs, TG
  uploads, web uploads). Локально — MinIO. Cloud-prod (Cloudflare R2) —
  backlog: той самий `aiobotocore`-клієнт, міняється тільки endpoint.

### Схема таблиць

Усі таблиці наслідують `Base` → `id BIGINT PK autoincrement`, `created_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ` (для append-only `messages`/`events` `updated_at` просто не апдейтиться). Json-поля — `JSONB`. Postgres ENUM-типи (`user_role`, `chat_source`, `message_role`, `event_kind`) — single source of truth у `app/models/enums.py`.

![Schema ER diagram](docs/schema.svg)

> **`docs/schema.svg` потребує регенерації** — додалися `notes.user_id`,
> таблиця `invites` (FK на users двічі: created_by + used_by) і **`turns`**
> (FK на chats/users + 2× messages: user_message + assistant_message).
> Актуальний source — `docs/schema.mmd`; відкрий у
> [mermaid.live](https://mermaid.live) → Export SVG → перезаписати
> `docs/schema.svg`.

**Як оновлювати картинку:**
- Джерело — `docs/schema.mmd` (Mermaid erDiagram) + `docs/schema.svg` (rendered)
- При зміні моделі: правиш `.mmd` → відкриваєш у [mermaid.live](https://mermaid.live) → Export SVG → перезаписуєш `docs/schema.svg`. Або: `npx -p @mermaid-js/mermaid-cli mmdc -i docs/schema.mmd -o docs/schema.svg` (тягне Chromium ~200MB).

**EventKind значення:** `THREAD_OPENED`, `THREAD_RESET`, `THREAD_LOST` (transport reconnect), `TURN_STARTED`, `TURN_COMPLETED`, `TURN_INTERRUPTED`, `TURN_FAILED`, `ATTACHMENT_RECEIVED`, `AUDIO_TRANSCRIBED`, `ERROR`.

**TurnStatus значення:** `STARTING` (row створений, codex ще не повернув
`turn/start`), `RUNNING` (got codex turn_id, worker streams), `COMPLETED`
(normal final), `FAILED` (timeout/exception/stream_dropped), `CANCELLED`
(explicit interrupt). Terminal = `(COMPLETED, FAILED, CANCELLED)` — frozen
set `TURN_TERMINAL_STATUSES`. `finalize_once` робить CAS UPDATE з гардом
`WHERE status IN ('STARTING','RUNNING')` → exactly-once.

**Alembic chain (поточний head — `e1f7a2b3c4d5`):**

```
4c560fef8dcd  init_schema
       ↓
8a3e1c5d4f02  add_user_role          (ENUM user_role + User.role)
       ↓
b1f2c3d4e5a6  add_user_password_hash (User.password_hash + display_name)
       ↓
c7d4e8a9b2f1  uploads_user_id        (Upload.user_id FK + index)
       ↓
d8e5f3a4b6c2  multi_user_web         (Note.user_id FK + invites table)
       ↓
e1f7a2b3c4d5  turns_lifecycle        (turns table + ENUM turn_status +
                                      turns_active_per_chat partial unique)  ← head
```

## Запуск

```bash
cp .env.example .env             # заповнити: TG_BOT_TOKEN, TG_ADMIN_USER_IDS,
                                 # REDIS_PASSWORD, SPEECHMATICS_API_KEY,
                                 # GOOGLE_TTS_API_KEY, MCP_CALLBACK_TOKEN,
                                 # SENTRY_DSN (опціонально), BUGSINK_AUTH_TOKEN
./docker/up.sh                   # бутстрап з усіма compose-файлами
```

Compose-файли розкидані по призначенню:

| Файл                              | Що піднімає                                                 |
|-----------------------------------|-------------------------------------------------------------|
| `docker/compose.yml`              | `codex-server` + `codex-worker` (TaskIQ) + `codex-web` (Vite dev) + postgres + redis + minio + minio-init |
| `docker/compose.codex.yml`        | `codex-cli` (admin sidecar) + `codex-cli-guest` (окремий проект `codex_codex`)  |
| `docker/compose.bugsink.yml`      | Self-hosted Sentry-compatible Bugsink на `:8089`            |
| `docker/compose.watch.yml`        | Watchdog контейнер — restart за `codex.watchdog=true` label |

`./docker/up.sh` піднімає все по черзі через спільну external network `codex_net`. `./docker/down.sh` валить усе.

Перед першим запуском гостьового sidecar — авторизуватись окремо (alt-ChatGPT
аккаунт, не admin):

```bash
docker compose -f docker/compose.codex.yml -p codex_codex run --rm codex-cli-guest codex login --device-auth
```

Без docker (тільки FastAPI, без Codex sidecar):

```bash
uv sync && uvicorn app.main:app --port 8088
```

Порти:
- `8088` — web UI (Vite dev → проксі на `codex-server:8000`)
- `5432`, `9000`/`9001` — postgres / minio API + console (debug only)
- `8089` — Bugsink UI
- `codex-server:8000` — internal, не виставлений назовні

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
  `timeout 30` щоб asyncpg-teardown не вішав boot). Head: `e1f7a2b3c4d5`
  (`turns_lifecycle` — `turns` table + `turn_status` ENUM + partial unique
  index на active turns).
- **Web auth (multi-user):** реєстрація email+password через
  `AuthService.Register(invite_token=...)`. Admin (визначається env
  `ADMIN_EMAIL`) при signup'і автоматично отримує `role=ADMIN` і обходить
  invite-перевірку — upsert на існуючий запис якщо він уже у БД. Решта
  юзерів — тільки за invite-токеном, який admin генерує через
  `AdminService.CreateInvite()` → `https://.../signup?invite=XYZ`. Endpoints
  під `/api/codex.v1.AdminService/*` захищені `require_admin`.
- **Rate-limit (RunTurn):** sliding-window у Redis, per-user. admin: none,
  web USER: 2 active / 30 per hour, TG guest: 1 active / 10 per hour. Reject
  з `ChatEvent.error{code='rate_limited'}` для web, текстова відповідь для TG.
- **Self-restart:** `/restart` у боті → `DockerControlService.restart_container`
  через `/var/run/docker.sock` (без `docker` CLI в образі). Admin-only.
- **Тести:** `uv run --group test pytest -q` (~95 у 15 файлах).

### Turn-as-a-Job lifecycle

`turns` table — single source of truth; `turn:{id}:events` Redis stream —
live транспорт; `codex-worker` (TaskIQ) — durable execution.

**Web flow.** `ChatService.RunTurn` → `_handle_active_turn` (8-крокова
симетрія для steer/interrupt/BUSY-фолбеки) → `_create_web_turn` (Postgres
INSERT з `status=STARTING` під partial unique fence → `IntegrityError =
BUSY`) → `execute_turn.kiq(turn.id, text, image_urls, voice_reply,
client_id)` → yield `TurnStartedEvent(turn_id)` → tail `turn:{id}:events`
поки не побачимо `DoneEvent`/`ErrorEvent` або `turns.status` стане
terminal. Reconnect: `TailTurn(turn_id, after_id)` — XREAD-ить tail, на
terminal-у з БД повертає synthetic terminal frame і закривається.

**TG flow.** Дзеркальна симетрія через `app/tg/turn/runner.TurnRunner` без
TaskIQ-enqueue — TG handler сам тримає stream-coroutine у тому ж процесі
(aiogram polling вже async). `_handle_active_tg_turn` повторює web 8-step
flow.

**Worker stage** (`app/services/turns/runner.execute_turn_inner`):

1. Idempotency guard — `turn.status in TERMINAL` → return.
2. `hold_turn_locks` — CAS на `turn:active:{chat_id}` з value=`str(turn_id)`.
   Outcome: `ACQUIRED` (новий run), `REDELIVERY` (same turn_id ⇒ silent
   skip), `ACTIVE_BUSY` (інший turn → finalize FAILED `TURN_BUSY`).
3. `heartbeat_loop` — фонова task оновлює `heartbeat_at` і refresh-ить
   active-lock TTL кожні 5s. Heartbeat матчить `(STARTING, RUNNING)` бо
   `mark_running` може ще не виконатись.
4. `open_codex_turn` → `stream_turn(turn_id=...)`. Stream-loop:
   - `on_started` callback робить `mark_running` (CAS STARTING→RUNNING +
     запис `codex_thread_id`/`codex_turn_id`).
   - `_check_steer_boundary` — peek `codex:steer-count:{turn_id}`, на ріст
     робить `_persist_segment` (INSERT partial assistant row з delta-text,
     без `client_id`).
   - `on_idle` — `consume_steer` keepalive або `probe_or_extend_idle`
     (`thread/read` status check).
   - Terminal: stream ALWAYS raises `CodexTurnTerminal(status, error_code,
     detail)`. Runner catches → `finalize_once`.
5. `finalize_once` — CAS UPDATE `turns SET status=? WHERE status IN
   ('STARTING','RUNNING')`. Перший повертає True, повторні — False (silent).

**Delta-segmented assistant persistence.** Steer розриває chronology:
кожен steer = новий "segment" → INSERT новий assistant row з delta-текстом
з моменту останнього cut. `seen_steer_count` локально, `peek_steer_count`
з Redis. Final assistant row отримує `meta.client_id` (anchor для
streaming-placeholder swap у Chat.svelte); partial-rows ключуються по
autoincrement `id`. Інваріант: тільки final має `client_id`, дублі
неможливі (раніше це ламало Svelte 5 keyed `{#each}`).

**Codex usage stream.** Event-driven, без periodic polling. Lifespan
викликає `bootstrap_usage()` один раз на startup — fetch admin+guest, publish
snapshot у Redis cache + pub/sub. Далі `runner.execute_turn_inner` (web) і
`TurnRunner._run_locked` (TG) після `finalize_once` тригерять
`usage_poller.schedule_refresh(sidecar)` — fire-and-forget refresh (момент
коли codex реально списав квоту). `StreamCodexUsage` RPC: bootstrap snapshot
з cache (1h TTL, instant) → subscribe на `codex:usage:{sidecar}` channel
за роллю користувача. `RefreshCodexUsage` unary RPC — manual force-fetch
кнопки "refresh" у `Usage.svelte`: server fetch sync + publish + return
snapshot (multi-tab consistency через pub/sub side effect).

- **TZ logs** — `utc=False` у `log_config`; timestamps з offset (`+03:00`).
  Якщо ship'ити логи у aggregator що очікує Z-suffix — перевір парсер.
- **Bugsink порт `:8089`** має власний BUGSINK_AUTH_TOKEN на UI + DSN-auth
  на ingest. Не виставляти прямо на public-net без nginx + rate-limiting;
  tailnet/VPN OK для соло.
- **Lint/types:** `uv run --group lint ruff check app/ tests/` + `pyright app/`.
- **Pre-commit hooks:** `uv sync --group lint && uv run pre-commit install` (одноразово). Далі кожен `git commit` ганяє `ruff check --fix`, `ruff format`, `pyright app/`, `pytest -q`. Bypass — `git commit --no-verify` (тільки exceptional).
- **Гарячий редеплой коду:** edit → save → `docker exec codex-server gunicornc -c reload`
  (workspace mount = source of truth, alembic зробить pending міграції).

## Структура

```
codex_server/
├── app/
│   ├── main.py                 FastAPI() + Sentry init + lifespan
│   │                           (TG polling, Codex sidecar warm-up,
│   │                            interrupt_active_turns при shutdown)
│   ├── config.py               pydantic-settings
│   ├── healthcheck.py          standalone HC для docker (без TCP probe)
│   ├── log_config.py           structlog + uvicorn JSON formatters
│   ├── api/                    HTTP endpoints
│   │   ├── health.py           GET /health (для docker healthcheck)
│   │   ├── tg_webhook.py       (опціональний webhook вхід; default polling)
│   │   └── docs/               OpenAPI helper docs
│   ├── mcp/                    MCP sub-app (/mcp/streamable, bearer auth)
│   │   ├── core.py             FastAPI sub-app + auth middleware
│   │   ├── errors.py
│   │   └── tools/              show_image (re-deliver image without regen)
│   ├── worker.py               TaskIQ `RedisStreamBroker` + result backend;
│   │                           точка входу для `codex-worker`
│   ├── rpc/                    Connect-RPC handlers
│   │   ├── auth.py             Login + Register (invite + ADMIN_EMAIL bypass) + Refresh
│   │   ├── admin.py            AdminService — CreateInvite/ListInvites/RevokeInvite/
│   │   │                       CreateUser/ListUsers (require_admin)
│   │   ├── health.py, user.py, message.py, event.py
│   │   ├── notes.py, uploads.py
│   │   ├── chat/               package — split kitchen-sink:
│   │   │   ├── service.py      ChatRPC: thin handlers + active-turn helpers
│   │   │   │                   (run_turn → kiq, tail_turn, interrupt_turn,
│   │   │   │                    steer_turn, stream_codex_usage)
│   │   │   ├── stream.py       codex events → pb pipeline (worker-side):
│   │   │   │                   token-delta coalesce, idle/error/cancel,
│   │   │   │                   steer-boundary persist segments, exactly-once
│   │   │   │                   terminal via CodexTurnTerminal raises
│   │   │   ├── mappers.py      ChatEvent ↔ pb + codex_usage_to_pb
│   │   │   ├── uploads.py      resolve upload_ids → codex-input
│   │   │   ├── tts.py          async post-turn TTS attach (voice in → voice out)
│   │   │   └── guards.py       pagination + ownership check
│   │   ├── _auth.py            JWT verify dep (require_user, require_admin)
│   │   ├── _mappers.py         ORM ↔ pb conversions (single source of truth)
│   │   └── router.py           ConnectRouter ASGI mount
│   ├── tg/                     aiogram polling
│   │   ├── service.py          bot lifecycle
│   │   ├── handlers.py         /new /stop /reset /restart + callbacks
│   │   ├── sessions.py         per-chat CodexClient store + admin/guest routing
│   │   ├── turn/               package — split kitchen-sink:
│   │   │   ├── runner.py       TurnRunner (orchestration entry)
│   │   │   ├── stream.py       codex event loop
│   │   │   ├── outcomes.py     handle_done / empty / dropped + send_response
│   │   │   ├── persistence.py  user/assistant message + journal
│   │   │   └── control.py      cancel / auto-reset / steer / emit_failure
│   │   ├── progress.py         status bubble + multi-bubble streaming
│   │   ├── output.py           send_text / send_voice_reply / send_attachment
│   │   ├── media.py            STT + attachment pipeline для inbound
│   │   └── markdown.py         TelegramMarkdown (singleton tg_markdown)
│   ├── models/                 SQLAlchemy ORM (User з UserRole+password_hash,
│   │                           Chat, Message, Event, Upload з user_id FK,
│   │                           Note з user_id FK, Invite (single-use signup);
│   │                           StrEnum kinds)
│   ├── services/               <resource>/{service.py, default.py, __init__.py}
│   │   ├── auth/               JWT issue/verify + password hashing (argon2)
│   │   ├── codex/              Codex CLI app-server клієнт (низький рівень):
│   │   │   ├── transport.py    JSON-RPC over WS + bounded notification queue
│   │   │   │                   (drop-oldest+WARN) + handler hook
│   │   │   ├── client.py       CodexClient — handshake, threads, run_turn
│   │   │   │                   (on_started/on_idle callbacks; raises
│   │   │   │                   StaleSidecarTurnError / StaleTurnStreamError)
│   │   │   ├── runner.py       open_codex_turn — RPC-side турн entry +
│   │   │   │                   quarantine_thread (24h TTL для broken threads)
│   │   │   ├── codex_remote.py Cross-worker control-RPC: send_steer_by_ids,
│   │   │   │                   send_interrupt_turn_id, note_steer/
│   │   │   │                   consume_steer (idle-watchdog keepalive),
│   │   │   │                   bump_steer_count/peek_steer_count (cut-segment
│   │   │   │                   signal). One-shot WS, не state-y.
│   │   │   ├── error_codes.py  CodexErrorCode enum (TURN_TIMEOUT, TURN_BUSY,
│   │   │   │                   STREAM_DROPPED, STALE_ACTIVE_TURN, CODEX_ERROR)
│   │   │   ├── collector.py    StreamCollector — shared state mutator
│   │   │   │                   (web/tg pipelines не дублюють absorb-logic)
│   │   │   ├── events/         types.py + translate.py + idle.py
│   │   │   └── history.py
│   │   ├── turns/              **Turn-as-a-Job lifecycle** (новий контракт):
│   │   │   ├── service.py      TurnService: create_starting / mark_running
│   │   │   │                   (CAS STARTING→RUNNING) / attach_assistant_message /
│   │   │   │                   heartbeat / finalize_once (CAS terminal-only) /
│   │   │   │                   get_active_for_chat / find_stale_active
│   │   │   ├── schemas.py      TurnCreate / TurnRow (Pydantic DTO)
│   │   │   ├── locks.py        hold_turn_locks ctx manager — CAS на
│   │   │   │                   `turn:active:{chat_id}` (ACQUIRED/REDELIVERY/
│   │   │   │                   ACTIVE_BUSY outcomes)
│   │   │   ├── stream.py       TurnStream: publish / tail / cleanup
│   │   │   │                   (XADD/XREAD per-turn Redis stream)
│   │   │   ├── status_map.py   CodexTurnStatus mapper (sidecar статуси →
│   │   │   │                   TurnStatus enum)
│   │   │   ├── probe.py        CodexTurnTerminal exception +
│   │   │   │                   interrupt_best_effort + probe_or_extend_idle
│   │   │   │                   (thread/read check на idle)
│   │   │   ├── recovery.py     reconcile_stale_turns (lifespan startup) +
│   │   │   │                   reconcile_if_stale (event-driven inline)
│   │   │   ├── runner.py       execute_turn_inner — idempotent task body +
│   │   │   │                   heartbeat_loop (фонова)
│   │   │   └── tasks.py        @broker.task execute_turn(...) — TaskIQ entry
│   │   ├── codex_usage/        Rate-limit observability:
│   │   │   ├── service.py      account/rateLimits/read обгортка (CodexUsage
│   │   │   │                   Pydantic DTO)
│   │   │   └── poller.py       Event-driven fan-out: `bootstrap_usage()`
│   │   │                       (один раз на startup) + `schedule_refresh()`
│   │   │                       тригер з runner-а після finalize. Pub/sub
│   │   │                       channel `codex:usage:{sidecar}` + cache
│   │   │                       snapshot (1h TTL) для bootstrap subscribers
│   │   ├── sessions/           Generic ChatSessionStore (TG/web — subclass'и)
│   │   ├── errors/             Sentry scrub_event + error classifier
│   │   ├── invites/            Single-use signup invite tokens (create/redeem)
│   │   ├── rate_limit/         Per-user RunTurn quota (active + sliding window)
│   │   ├── chats/, users/, messages/, events/, uploads/, notes/
│   │   ├── bus/                Redis pub/sub fan-out для ChatEvent (legacy
│   │   │                       broadcast канал `chat:{id}:events`)
│   │   ├── cache/              Redis client singleton (`cache` + `binary_cache`)
│   │   ├── storage/            S3/MinIO via aiobotocore
│   │   ├── stt/                Speechmatics batch v2
│   │   ├── tts/                Google Cloud TTS (REST v1, OGG_OPUS)
│   │   └── docker/             host docker daemon control (/restart)
│   ├── utils/paths.py          trusted-root resolver для tool-supplied paths
│   ├── deps/                   FastAPI auth dep
│   └── grpc_generated/         Connect stubs (gitignored regen via scripts/gen-proto.sh)
├── web/                        Svelte 5 + Vite + Tailwind 4
│   ├── src/
│   │   ├── App.svelte
│   │   ├── main.ts
│   │   ├── features/
│   │   │   ├── auth/           Login.svelte + Signup.svelte + auth.ts (HttpOnly cookie)
│   │   │   ├── chat/           Chat / ChatList / MessageList / Message /
│   │   │   │                   Composer / Attachment / ToolCall /
│   │   │   │                   CompletedTools / Usage / typewriter /
│   │   │   │                   turnSignal / markdown
│   │   │   └── notes/          Notes.svelte
│   │   ├── shared/
│   │   │   ├── components/     Spinner
│   │   │   └── lib/            transport / clients / theme / time / token
│   │   └── gen/codex/v1/       *_pb.ts (regen `npm run gen` через buf)
│   ├── buf.gen.yaml            protoc-gen-es → src/gen
│   ├── package.json            scripts: gen / dev / build / check
│   └── vite.config.ts          /api proxy → codex-server:8000
├── protos/codex/v1/            *.proto (auth, chat, common, event, message,
│                               user, notes, uploads) — single source of truth
│                               для server stubs + client stubs
├── migrations/versions/        alembic chain:
│                                 4c560fef8dcd → 8a3e1c5d4f02 →
│                                 b1f2c3d4e5a6 → c7d4e8a9b2f1 →
│                                 d8e5f3a4b6c2 → e1f7a2b3c4d5 (head)
├── tests/                      pytest (~95 у 15 файлах):
│                               admin_rpc, auth_rpc/service, chat_rpc,
│                               codex_collector/runner/streaming/translate,
│                               messages_service, rate_limit,
│                               tg_markdown/progress/turn, turn_service,
│                               user_service
├── docker/
│   ├── codex/                  Dockerfile + entrypoint + AGENTS-guest.md
│   ├── server/                 Dockerfile (multi-stage, free-threaded 3.14t)
│   │                           + entrypoint.sh (alembic upgrade head + start)
│   ├── web/                    ← порожній; production single-image build TODO
│   ├── watchdog/watch.sh       docker events → restart по label
│   ├── compose.yml             app stack (codex-server + codex-worker + codex-web + pg/redis/minio)
│   ├── compose.codex.yml       Codex CLI sidecars (admin + guest)
│   ├── compose.bugsink.yml     self-hosted Sentry-compatible (порт :8089)
│   ├── compose.watch.yml       watchdog контейнер
│   ├── up.sh / down.sh         оркестрація всіх compose-файлів
├── estimates/                  (gitignored — особисті планувальні нотатки)
│   └── INTEGRATION.md          phase-by-phase status + TODO
├── docs/
│   ├── schema.svg              ER diagram (картинка для README, hand-written)
│   └── schema.mmd              Mermaid source (правити тут → regen .svg)
├── scripts/gen-proto.sh        Python pb2 + connect stubs
├── gunicorn.conf.py            workers, reload, signal handling
├── pyproject.toml              Python 3.14 + deps (uv-managed)
├── alembic.ini
├── AGENTS.md                   Codex CLI persona contract (admin)
└── README.md                   ← цей файл
```
