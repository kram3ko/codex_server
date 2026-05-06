from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    OPENAI_API_KEY: str = ""

    # Postgres
    DATABASE_URL: str = "postgresql+asyncpg://codex:codex@localhost:5432/codex"

    # Object storage (MinIO local / R2 prod, S3-compat)
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "personal-codex"
    S3_REGION: str = "us-east-1"

    # Telegram
    TG_BOT_TOKEN: str = ""
    TG_ALLOWED_USER_ID: int = 0

    # Web chat auth
    WEB_API_TOKEN: str = ""


settings = Settings()
