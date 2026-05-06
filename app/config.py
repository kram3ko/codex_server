from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM / Codex sidecar ---
    OPENAI_API_KEY: str = ""
    CODEX_APP_SERVER_URL: str = "ws://localhost:4500"

    # --- Postgres ---
    DATABASE_URL: str = "postgresql+asyncpg://codex:codex@localhost:5432/codex"
    DB_ECHO: bool = False

    # --- Object storage (MinIO local / R2 prod) ---
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "personal-codex"
    S3_REGION: str = "us-east-1"

    # --- Telegram ---
    TG_BOT_TOKEN: str = ""
    TG_ALLOWED_USER_ID: int = 0

    # --- Auth (single-user JWT) ---
    # WEB_API_TOKEN — "пароль", який клієнт обмінює на короткоживучий JWT
    # через POST /auth/token. JWT_SECRET — окремий ключ для підпису токена.
    WEB_API_TOKEN: str = ""
    JWT_SECRET: str = "change-me-please"
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_HOURS: int = 24


settings = Settings()
