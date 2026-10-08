from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserProfile(BaseModel):
    """Адмін-доповнення до protobuf User: поля, яких немає у RPC-контракті."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="users.id як string — JS не тримає int64 без втрат.")
    tg_username: str | None
    last_active_at: datetime | None = Field(
        default=None, description="created_at останнього turn юзера; None — ще не писав."
    )
