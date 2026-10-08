from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models import User, UserRole
from app.models.runtime_setting import TG_BOT_API_DOWNLOAD_LIMIT_MB, RuntimeSetting

MB = 1024 * 1024


class UserKind(StrEnum):
    ADMIN = "admin"
    WEB_USER = "web_user"
    TG_GUEST = "tg_guest"


def user_kind(user: User) -> UserKind:
    if user.role == UserRole.ADMIN:
        return UserKind.ADMIN
    if user.email:
        return UserKind.WEB_USER
    return UserKind.TG_GUEST


class TurnLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    active: int = Field(ge=0, description="Одночасних turn-ів на юзера; 0 — без ліміту.")
    hourly: int = Field(ge=0, description="Turn-ів за ковзну годину; 0 — без ліміту.")


UNLIMITED = TurnLimits(active=0, hourly=0)


class RuntimeSettings(BaseModel):
    """Boundary DTO адмін-API ↔ строка `runtime_settings`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    web_user: TurnLimits = TurnLimits(active=2, hourly=0)
    tg_guest: TurnLimits = TurnLimits(active=3, hourly=0)
    tg_guest_max_upload_mb: int = Field(
        default=TG_BOT_API_DOWNLOAD_LIMIT_MB,
        ge=0,
        le=TG_BOT_API_DOWNLOAD_LIMIT_MB,
        description="Макс. розмір вкладення TG-гостя у MB; 0 — без ліміту (стеля Bot API 20).",
    )

    @classmethod
    def from_row(cls, row: RuntimeSetting) -> RuntimeSettings:
        return cls(
            web_user=TurnLimits(active=row.web_user_active, hourly=row.web_user_hourly),
            tg_guest=TurnLimits(active=row.tg_guest_active, hourly=row.tg_guest_hourly),
            tg_guest_max_upload_mb=row.tg_guest_max_upload_mb,
        )

    def apply_to(self, row: RuntimeSetting) -> None:
        row.web_user_active = self.web_user.active
        row.web_user_hourly = self.web_user.hourly
        row.tg_guest_active = self.tg_guest.active
        row.tg_guest_hourly = self.tg_guest.hourly
        row.tg_guest_max_upload_mb = self.tg_guest_max_upload_mb

    def turn_limits(self, kind: UserKind) -> TurnLimits:
        match kind:
            case UserKind.ADMIN:
                return UNLIMITED
            case UserKind.WEB_USER:
                return self.web_user
            case UserKind.TG_GUEST:
                return self.tg_guest

    def max_upload_bytes(self, kind: UserKind) -> int | None:
        if kind is not UserKind.TG_GUEST or self.tg_guest_max_upload_mb == 0:
            return None
        return self.tg_guest_max_upload_mb * MB
