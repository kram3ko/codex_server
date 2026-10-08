from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class IntegrationKind(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    SSH = "ssh"
    TELEGRAM = "telegram"


class TelegramOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admin_ids: list[int] = Field(default_factory=list)
    webhook_url: str = ""


class IntegrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    kind: IntegrationKind
    host: str = Field(default="", max_length=253)
    username: str = Field(default="", max_length=100)
    secret: SecretStr | None = Field(default=None, max_length=65536)
    enabled: bool = True
    telegram: TelegramOptions = Field(default_factory=TelegramOptions)


class IntegrationView(BaseModel):
    id: int
    name: str
    kind: IntegrationKind
    host: str
    username: str
    enabled: bool
    has_secret: bool
    created_at: datetime
    updated_at: datetime
    telegram: TelegramOptions
    fingerprint: str | None = None
    bot_username: str | None = None


class ChatUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replies_enabled: bool
