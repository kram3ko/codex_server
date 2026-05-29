"""Telegram-specific bootstrap for shared Codex chat sessions."""

from pydantic import BaseModel, ConfigDict, Field

from app.db.base import SessionLocal
from app.models import EventKind, UserRole
from app.services.chats.default import chat_service
from app.services.events.default import event_service
from app.services.sessions.store import (
    ChatSession,
    SessionBootstrap,
)
from app.services.sessions.store import (
    ChatSessionStore as BaseChatSessionStore,
)
from app.services.users.default import user_service


class TGSessionBootstrap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tg_user_id: int = Field(description="Telegram-side user id (для admin-role lookup).")
    display_name: str | None = Field(default=None, description="Display name з aiogram message.")


TGSessionKey = tuple[int, int | None]


class ChatSessionStore(BaseChatSessionStore[TGSessionKey, TGSessionBootstrap]):
    async def get_or_open(
        self,
        tg_user_id: int,
        tg_chat_id: int,
        tg_message_thread_id: int | None = None,
        display_name: str | None = None,
    ) -> ChatSession:
        return await self._get_or_open(
            (tg_chat_id, tg_message_thread_id),
            TGSessionBootstrap(tg_user_id=tg_user_id, display_name=display_name),
        )

    async def _bootstrap(
        self,
        key: TGSessionKey,
        bootstrap_arg: TGSessionBootstrap,
    ) -> SessionBootstrap:
        tg_chat_id, tg_message_thread_id = key
        async with SessionLocal() as db:
            user = await user_service.get_or_create_by_tg(
                db,
                bootstrap_arg.tg_user_id,
                bootstrap_arg.display_name,
            )
            chat = await chat_service.get_or_create_for_tg(
                db, user.id, tg_chat_id, tg_message_thread_id
            )
            is_admin = user.role == UserRole.ADMIN
            await db.commit()
            return SessionBootstrap(
                db_user_id=user.id,
                db_chat_id=chat.id,
                is_admin=is_admin,
            )

    async def _on_turn_interrupted(self, session: ChatSession) -> None:
        async with SessionLocal() as db:
            await event_service.emit(
                db,
                EventKind.TURN_INTERRUPTED,
                chat_id=session.db_chat_id,
                user_id=session.db_user_id,
            )
            await db.commit()
