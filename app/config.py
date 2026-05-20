from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 32 bytes = HMAC-SHA256 block size, RFC 7518 §3.2 рекомендований мінімум.
_JWT_SECRET_MIN_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Codex sidecar ---
    # OPENAI_API_KEY живе тільки у .env → docker-compose передає його у
    # codex-cli контейнер. Нашому FastAPI він не треба як settings.
    CODEX_CLI_URL: str = "ws://localhost:4500"
    CODEX_CLI_GUEST_URL: str = "ws://codex-cli-guest:4500"
    CODEX_CWD: str = "/home/codex/workspace"
    CODEX_APPROVAL_POLICY: str = "never"
    CODEX_SANDBOX: str = "danger-full-access"
    CODEX_REQUEST_TIMEOUT_SECONDS: float = 600.0
    CODEX_THREAD_REUSE_ENABLED: bool = True
    CODEX_REASONING_EFFORT: str = "medium"
    # Bounded notification queue (per CodexClient instance). Drop-oldest на
    # переповненні з WARN `app_server_notification_dropped`. Розмір треба
    # сайзити під concurrent-turn-count на цей конкретний sidecar:
    #   admin: 1 owner + ~50 trusted users → 400 starting point
    #   guest: ~500 TG-юзерів → 1000+ під реальний burst
    # Емпірика, моніторити через Bugsink.
    CODEX_NOTIFICATION_QUEUE_MAX_ADMIN: int = 400
    CODEX_NOTIFICATION_QUEUE_MAX_GUEST: int = 1000

    # Server-side coalesce token-delta'ів у Connect-RPC stream: buffer
    # послідовних `TokenEvent.delta` доки сумарний UTF-8 розмір ≥ цієї
    # межі, потім flush одним `ChatEvent`. Знижує HTTP/2 frame overhead
    # і тиск на upstream queue при concurrent гостях. 0 → вимикає coalesce
    # (raw passthrough). 256 байт ≈ 1-2 рядки прози → непомітно у typewriter.
    # ToolCall/Result/Done/Error завжди форсять flush + passthrough.
    CHAT_TOKEN_COALESCE_BYTES: int = 256
    # Поріг "шторму" stale-notif'ів після resume: skip idle-timeout якщо
    # підряд приходить ≥N notif'ів від старого turn_id і 0 від нового.
    # Емпірика; калібрувати по `codex_stale_turn_notification_ignored`.
    CODEX_STALE_STORM_THRESHOLD: int = 25

    # --- Codex WS handshake auth (CLI 0.131+ `--ws-auth signed-bearer-token`) ---
    # Per-audience HS256 shared secrets. Leak одного не дає доступ до іншого
    # sidecar-у. Generate via: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
    CODEX_WS_SECRET_ADMIN: str = ""
    CODEX_WS_SECRET_GUEST: str = ""
    CODEX_WS_ISSUER: str = "codex-server"
    CODEX_WS_AUDIENCE_ADMIN: str = "codex-cli-admin"
    CODEX_WS_AUDIENCE_GUEST: str = "codex-cli-guest"

    # --- Speech-to-text (Speechmatics batch v2) ---
    # `language='auto'` triggers Speechmatics Language Identification.
    SPEECHMATICS_API_KEY: str = ""
    SPEECHMATICS_LANGUAGE: str = "auto"
    SPEECHMATICS_OPERATING_POINT: str = "enhanced"
    # Upper-bound на один transcribe-job. TG voice ноти зазвичай 30-90s; 3хв з запасом.
    STT_TIMEOUT_SECONDS: float = 180.0
    # Polling cadence на /jobs/{id} у Speechmatics.
    STT_POLL_INTERVAL_SECONDS: float = 1.5

    # --- Text-to-speech (Google Cloud TTS, REST + API key) ---
    GOOGLE_TTS_API_KEY: str = ""
    GOOGLE_TTS_AUDIO_ENCODING: str = "MP3"

    # --- Postgres ---
    DATABASE_URL: str = "postgresql+asyncpg://codex:codex@localhost:5432/codex"
    DB_ECHO: bool = False

    # --- Redis (cache + future pub/sub between sidecars) ---
    # URL must include the password if REDIS_PASSWORD is set on the server.
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_PASSWORD: str = ""

    # --- Object storage (MinIO local / R2 prod) ---
    STORAGE_BACKEND: str = "s3"  # s3 | dropbox | gdrive (future)
    # Presigned URL TTL: default 1 година, hard-cap 24 години (захист від
    # leaked links). Ops може налаштувати під свій security policy.
    S3_PRESIGNED_DEFAULT_TTL_SECONDS: int = 3600
    S3_PRESIGNED_MAX_TTL_SECONDS: int = 24 * 3600
    S3_ENDPOINT: str = "http://localhost:9000"
    # Browser-facing prefix/endpoint для presigned URLs.
    # У compose: S3_ENDPOINT=http://minio:9000 (internal),
    # S3_PUBLIC_ENDPOINT=/minio (same-origin proxy via web/nginx).
    S3_PUBLIC_ENDPOINT: str = ""
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "personal-codex"
    S3_REGION: str = "us-east-1"

    # --- Telegram ---
    TG_BOT_TOKEN: str = ""
    # Bootstrap-only: TG user_ids що отримають `UserRole.ADMIN` при першому
    # створенні (`UserService.get_or_create_by_tg`). Існуючих юзерів promote'ить
    # `ensure_admin_roles()` на startup. Дальше адмінів додавати через psql /
    # майбутній RPC `UserService.PromoteToAdmin`. Empty set → нікого автоматично
    # admin'ом не робимо.
    TG_ADMIN_USER_IDS: set[int] = set()
    TG_PROGRESS_DELAY_SECONDS: float = 0.0
    TG_DRAFT_ENABLED: bool = False
    # Idle cap на турн: якщо Codex sidecar мовчить довше цього часу між
    # events/chunks → interrupt() і звільняємо turn_lock.
    TG_TURN_TIMEOUT_SECONDS: float = 300.0
    WEB_TURN_TIMEOUT_SECONDS: float = 300.0
    # Per-turn Redis stream tunables (`app/services/turns/stream.py`).
    # MAXLEN cap — sliding window поверх XADD; ~5000 events покриває довгий
    # agentic turn з 50+ tool calls без втрати ранніх token-ів.
    TURN_STREAM_MAX_EVENTS: int = 5000
    # 24h — terminal event живе достатньо для будь-якого reasonable reconnect
    # window-у (закрив лептоп, відкрив через день). Cleanup compress-ить до 60s
    # після finalize.
    TURN_STREAM_TTL_S: int = 86_400
    # Short BLOCK у XREAD щоб tail-loop не висів довго на dead stream —
    # caller повторно перевіряє `turn.status` між iterations.
    TURN_STREAM_TAIL_BLOCK_MS: int = 2000
    TG_WEBHOOK_URL: str = ""
    TG_WEBHOOK_SECRET: str = ""

    # --- Auth (email + password → JWT) ---
    # ADMIN_EMAIL/ADMIN_PASSWORD — bootstrap акаунт: на startup створюється
    # юзер з таким email і `password_hash = argon2id(ADMIN_PASSWORD)`. Якщо
    # ADMIN_PASSWORD змінюється у .env — хеш у БД переписується. JWT_SECRET —
    # ключ для підпису access-токена.
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_HOURS: int = 24
    # JWT доставляється у HttpOnly cookie (per CLAUDE.md §5 — XSS-захист).
    # `Secure` лишаємо False у dev (HTTP localhost відмовиться set'нути Secure);
    # у prod виставляти True (HTTPS обов'язково).
    COOKIES_SECURE: bool = False
    # CORS allowlist — explicit origins, wildcard несумісне з cookie credentials.
    CORS_ALLOWED_ORIGINS: list[str] = []

    # --- MCP (Codex CLI ↔ FastAPI tools bridge) ---
    # Bearer token який Codex CLI шле у Authorization при stream-HTTP виклику
    # /mcp/streamable. Тільки шлях з docker-network доступний; токен — друга
    # лінія захисту. Empty = MCP routes відкриті, не для prod.
    MCP_CALLBACK_TOKEN: str = ""

    # --- Health probe ---
    # Docker healthcheck зовнішній timeout — 5s; внутрішній probe має fit'нутися.
    HEALTH_PROBE_TIMEOUT_SECONDS: float = 3.0

    # --- Bugsink (Sentry-SDK-compatible error tracker) ---
    # Empty SENTRY_DSN → sentry_sdk.init no-op. BUGSINK_AUTH_TOKEN — Bearer
    # для codex-server → Bugsink REST API (юзають MCP tools `list_errors`/
    # `get_error`).
    SENTRY_DSN: str = ""
    SENTRY_RELEASE: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    BUGSINK_INTERNAL_URL: str = "http://bugsink:8000"
    BUGSINK_AUTH_TOKEN: str = ""

    @field_validator("TG_ADMIN_USER_IDS", mode="before")
    @classmethod
    def _split_user_ids(cls, value: object) -> object:
        if isinstance(value, int):
            return {value}
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return set()
        return {int(part) for part in stripped.split(",") if part.strip()}

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return [part.strip() for part in value.split(",") if part.strip()]

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> Settings:
        # Fail-fast щоб порожнє/коротке значення з .env не підписувало токени
        # weak ключем. Generate via: secrets.token_urlsafe(64).
        if len(self.JWT_SECRET.strip()) < _JWT_SECRET_MIN_LEN:
            raise ValueError(
                f"JWT_SECRET must be ≥{_JWT_SECRET_MIN_LEN} chars (was "
                f"{len(self.JWT_SECRET.strip())}); set in .env"
            )
        for field in ("CODEX_WS_SECRET_ADMIN", "CODEX_WS_SECRET_GUEST"):
            value = getattr(self, field).strip()
            if len(value) < _JWT_SECRET_MIN_LEN:
                raise ValueError(
                    f"{field} must be ≥{_JWT_SECRET_MIN_LEN} chars (was "
                    f"{len(value)}); set in .env"
                )
        return self


settings = Settings()
