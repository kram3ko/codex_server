"""Web-specific bootstrap for shared Codex chat sessions."""

from app.db.base import SessionLocal
from app.services.chats.default import chat_service
from app.services.sessions.store import (
    ChatSession,
    SessionBootstrap,
)
from app.services.sessions.store import (
    ChatSessionStore as BaseChatSessionStore,
)
from app.services.users.default import user_service

_WEB_USER_EMAIL = "web@codex.local"


class WebSessionStore(BaseChatSessionStore[int, None]):
    async def get_or_open(self, web_user_id: int) -> ChatSession:
        return await self._get_or_open(web_user_id, None)

    async def _bootstrap(self, key: int, bootstrap_arg: None) -> SessionBootstrap:
        async with SessionLocal() as db:
            user = await user_service.get(db, key)
            if user is None:
                user = await user_service.get_or_create_by_email(db, _WEB_USER_EMAIL)
            chat = await chat_service.get_or_create_for_web(db, user.id)
            await db.commit()
            return SessionBootstrap(
                db_user_id=user.id,
                db_chat_id=chat.id,
                stored_thread_id=chat.codex_thread_id,
                # Browser chat uses the primary admin sidecar, not guest.
                is_admin=True,
            )


web_sessions = WebSessionStore()
