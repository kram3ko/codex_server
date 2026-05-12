"""Telegram-specific bootstrap for shared Codex chat sessions."""

from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class TGSessionBootstrap:
    tg_user_id: int
    display_name: str | None = None


class ChatSessionStore(BaseChatSessionStore[int, TGSessionBootstrap]):
    async def get_or_open(
        self,
        tg_user_id: int,
        tg_chat_id: int,
        display_name: str | None = None,
    ) -> ChatSession:
        return await self._get_or_open(
            tg_chat_id,
            TGSessionBootstrap(tg_user_id=tg_user_id, display_name=display_name),
        )

    async def _bootstrap(
        self,
        key: int,
        bootstrap_arg: TGSessionBootstrap,
    ) -> SessionBootstrap:
        async with SessionLocal() as db:
            user = await user_service.get_or_create_by_tg(
                db,
                bootstrap_arg.tg_user_id,
                bootstrap_arg.display_name,
            )
            chat = await chat_service.get_or_create_for_tg(db, user.id, key)
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
