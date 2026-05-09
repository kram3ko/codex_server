"""Application user — link point between TG, web and any future surface."""

from sqlalchemy import BigInteger, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ENUM_NAMES, UserRole


class User(Base):
    __tablename__ = "users"

    tg_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        unique=True,
        nullable=True,
        index=True,
    )
    email: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name=ENUM_NAMES[UserRole]),
        nullable=False,
        default=UserRole.USER,
        server_default=UserRole.USER.value,
    )
