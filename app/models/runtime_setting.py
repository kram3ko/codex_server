from sqlalchemy import CheckConstraint, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SINGLETON_ID = 1
TG_BOT_API_DOWNLOAD_LIMIT_MB = 20


class RuntimeSetting(Base):
    """Єдина строка (id=1) з адмін-керованими лімітами; 0 = ліміт вимкнено."""

    __tablename__ = "runtime_settings"
    __table_args__ = (
        CheckConstraint(f"id = {SINGLETON_ID}", name="runtime_settings_singleton"),
        CheckConstraint("web_user_active >= 0", name="runtime_settings_web_user_active"),
        CheckConstraint("web_user_hourly >= 0", name="runtime_settings_web_user_hourly"),
        CheckConstraint("tg_guest_active >= 0", name="runtime_settings_tg_guest_active"),
        CheckConstraint("tg_guest_hourly >= 0", name="runtime_settings_tg_guest_hourly"),
        CheckConstraint(
            f"tg_guest_max_upload_mb BETWEEN 0 AND {TG_BOT_API_DOWNLOAD_LIMIT_MB}",
            name="runtime_settings_tg_guest_max_upload_mb",
        ),
    )

    web_user_active: Mapped[int] = mapped_column(Integer, server_default="2")
    web_user_hourly: Mapped[int] = mapped_column(Integer, server_default="0")
    tg_guest_active: Mapped[int] = mapped_column(Integer, server_default="3")
    tg_guest_hourly: Mapped[int] = mapped_column(Integer, server_default="0")
    tg_guest_max_upload_mb: Mapped[int] = mapped_column(
        Integer, server_default=str(TG_BOT_API_DOWNLOAD_LIMIT_MB)
    )
