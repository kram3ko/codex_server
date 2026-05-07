from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Codex sidecar ---
    # OPENAI_API_KEY живе тільки у .env → docker-compose передає його у
    # codex-app-server контейнер. Нашій FastAPI його не треба як settings.
    CODEX_APP_SERVER_URL: str = "ws://localhost:4500"
    CODEX_CWD: str = "/home/codex/workspace"
    CODEX_APPROVAL_POLICY: str = "never"
    CODEX_SANDBOX: str = "danger-full-access"
    CODEX_REQUEST_TIMEOUT_SECONDS: float = 500.0
    # Reuse stored codex_thread_id for token economy (sidecar resumes from disk).
    # Disable якщо побачимо проблеми (silent-finalization тощо) — кожен turn
    # тоді відкриватиме fresh thread.
    CODEX_THREAD_REUSE_ENABLED: bool = True

    # --- Speech-to-text (Speechmatics batch v2) ---
    # `language='auto'` triggers Speechmatics Language Identification.
    SPEECHMATICS_API_KEY: str = ""
    SPEECHMATICS_LANGUAGE: str = "auto"
    SPEECHMATICS_OPERATING_POINT: str = "enhanced"

    # --- Postgres ---
    DATABASE_URL: str = "postgresql+asyncpg://codex:codex@localhost:5432/codex"
    DB_ECHO: bool = False

    # --- Redis (cache + future pub/sub between sidecars) ---
    # URL must include the password if REDIS_PASSWORD is set on the server.
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_PASSWORD: str = ""

    # --- Object storage (MinIO local / R2 prod) ---
    STORAGE_BACKEND: str = "s3"  # s3 | dropbox | gdrive (future)
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "personal-codex"
    S3_REGION: str = "us-east-1"

    # --- Telegram ---
    TG_BOT_TOKEN: str = ""
    # Comma-separated whitelist of Telegram user ids. Empty set → bot accepts
    # anyone (don't ship like that). Filter applied in tg/service.py.
    TG_ALLOWED_USER_IDS: set[int] = set()
    TG_PROGRESS_DELAY_SECONDS: float = 0.0
    TG_DRAFT_ENABLED: bool = False
    TG_POLLING_LOCK_TTL_SECONDS: int = 60

    # --- Auth (single-user JWT) ---
    # WEB_API_TOKEN — "пароль", який клієнт обмінює на короткоживучий JWT
    # через POST /auth/token. JWT_SECRET — окремий ключ для підпису токена.
    WEB_API_TOKEN: str = ""
    JWT_SECRET: str = "change-me-please"
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_HOURS: int = 24

    @field_validator("TG_ALLOWED_USER_IDS", mode="before")
    @classmethod
    def _split_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return set()
            return {int(part) for part in stripped.split(",") if part.strip()}
        return value


settings = Settings()
