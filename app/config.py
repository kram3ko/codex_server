"""Runtime settings.

Кожна section (`# --- Foo ---`) — один підсистема. Поля без коментаря
тривіальні; коментар лише там де WHY non-obvious (calibration, security
invariant, runtime contract).
"""

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_JWT_SECRET_MIN_LEN = 32  # HMAC-SHA256 block size, RFC 7518 §3.2


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- SSH key vault (admin codex agent) ---
    # codex-server пише сюди; той самий volume змонтований у codex-cli як `~/.ssh`.
    SSH_VAULT_DIR: str = "/ssh-vault"

    # --- Codex sidecar ---
    CODEX_CLI_URL: str = "ws://localhost:4500"
    CODEX_CLI_GUEST_URL: str = "ws://codex-cli-guest:4500"
    CODEX_CWD: str = "/home/codex/workspace"
    CODEX_APPROVAL_POLICY: str = "never"
    CODEX_SANDBOX: str = "danger-full-access"
    CODEX_REQUEST_TIMEOUT_SECONDS: float = 600.0
    CODEX_THREAD_REUSE_ENABLED: bool = True
    CODEX_REASONING_EFFORT: str = "medium"
    # Drop-oldest queue. Sizing: admin ≈ 1 owner; guest ≈ 500 TG users.
    CODEX_NOTIFICATION_QUEUE_MAX_ADMIN: int = 400
    CODEX_NOTIFICATION_QUEUE_MAX_GUEST: int = 1000
    # Coalesce TokenEvent deltas (HTTP/2 frame pressure). 0 disables.
    CHAT_TOKEN_COALESCE_BYTES: int = 256
    # Stale-notif storm threshold після resume.
    CODEX_STALE_STORM_THRESHOLD: int = 25
    # Hard cap: `tool-active = alive` anti-pattern (openai/codex#4337).
    CODEX_TURN_HARD_TIMEOUT_S: float = 600.0
    # Rate-limit forced L2 probe (`thread/read`) under active items.
    CODEX_IDLE_PROBE_INTERVAL_S: float = 30.0

    # --- Codex WS auth (CLI 0.131+ signed-bearer-token) ---
    CODEX_WS_SECRET_ADMIN: str = ""
    CODEX_WS_SECRET_GUEST: str = ""
    CODEX_WS_ISSUER: str = "codex-server"
    CODEX_WS_AUDIENCE_ADMIN: str = "codex-cli-admin"
    CODEX_WS_AUDIENCE_GUEST: str = "codex-cli-guest"

    # --- Speech-to-text (Speechmatics batch v2) ---
    SPEECHMATICS_API_KEY: str = ""
    SPEECHMATICS_LANGUAGE: str = "auto"
    SPEECHMATICS_OPERATING_POINT: str = "enhanced"
    STT_TIMEOUT_SECONDS: float = 180.0
    STT_POLL_INTERVAL_SECONDS: float = 1.5

    # --- Text-to-speech (Google Cloud TTS, REST + API key) ---
    GOOGLE_TTS_API_KEY: str = ""
    GOOGLE_TTS_AUDIO_ENCODING: str = "MP3"

    # --- Postgres ---
    DATABASE_URL: str = "postgresql+asyncpg://codex:codex@localhost:5432/codex"
    DB_ECHO: bool = False

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_PASSWORD: str = ""

    # --- Object storage (MinIO local / R2 prod) ---
    STORAGE_BACKEND: str = "s3"
    S3_PRESIGNED_DEFAULT_TTL_SECONDS: int = 3600
    S3_PRESIGNED_MAX_TTL_SECONDS: int = 24 * 3600
    S3_ENDPOINT: str = "http://localhost:9000"
    # Browser-facing prefix (compose proxy via web/nginx).
    S3_PUBLIC_ENDPOINT: str = ""
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "personal-codex"
    S3_REGION: str = "us-east-1"

    # --- Telegram ---
    TG_BOT_TOKEN: str = ""
    # Bootstrap-only: promote to ADMIN at first creation.
    TG_ADMIN_USER_IDS: set[int] = set()
    TG_PROGRESS_DELAY_SECONDS: float = 0.0
    TG_DRAFT_ENABLED: bool = False
    TG_TURN_TIMEOUT_SECONDS: float = 300.0
    WEB_TURN_TIMEOUT_SECONDS: float = 300.0
    TURN_STREAM_MAX_EVENTS: int = 5000
    TURN_STREAM_TTL_S: int = 86_400
    TURN_STREAM_TAIL_BLOCK_MS: int = 2000
    TG_WEBHOOK_URL: str = ""
    TG_WEBHOOK_SECRET: str = ""

    # --- Auth (email + password → JWT) ---
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_HOURS: int = 24
    # False у dev (HTTP localhost), True у prod (HTTPS).
    COOKIES_SECURE: bool = False
    CORS_ALLOWED_ORIGINS: list[str] = []

    # --- MCP (Codex CLI ↔ FastAPI bridge) ---
    MCP_CALLBACK_TOKEN: str = ""
    # HS256 secret для per-turn JWT що ін'єктимо у промпт-header.
    # Модель форвардить як `authz` arg → MCP tool верифікує signature stateless.
    MCP_AUTHZ_SECRET: str = ""
    MCP_AUTHZ_TTL_S: int = 1800

    # --- Health probe ---
    HEALTH_PROBE_TIMEOUT_SECONDS: float = 3.0

    # --- Bugsink (Sentry-SDK error tracker) ---
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
        if len(self.JWT_SECRET.strip()) < _JWT_SECRET_MIN_LEN:
            raise ValueError(
                f"JWT_SECRET must be ≥{_JWT_SECRET_MIN_LEN} chars (was "
                f"{len(self.JWT_SECRET.strip())}); set in .env"
            )
        for field in (
            "CODEX_WS_SECRET_ADMIN",
            "CODEX_WS_SECRET_GUEST",
            "MCP_AUTHZ_SECRET",
        ):
            value = getattr(self, field).strip()
            if len(value) < _JWT_SECRET_MIN_LEN:
                raise ValueError(
                    f"{field} must be ≥{_JWT_SECRET_MIN_LEN} chars (was {len(value)}); set in .env"
                )
        return self


settings = Settings()
