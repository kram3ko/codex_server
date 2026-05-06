"""Thin aiogram handlers — delegate to TurnRunner / ChatSessionStore."""

import structlog
from aiogram.types import CallbackQuery, Message

from app.db.base import SessionLocal
from app.models import EventKind
from app.services.events.default import event_service
from app.tg.formatting import tg_html
from app.tg.progress import CB_TURN_NEW, CB_TURN_STEER, CB_TURN_STOP
from app.tg.sessions import ChatSessionStore
from app.tg.turn import TurnRunner, cancel_turn

log = structlog.get_logger(__name__)


class TGHandlers:
    def __init__(self, sessions: ChatSessionStore, runner: TurnRunner) -> None:
        self._sessions = sessions
        self._runner = runner

    async def on_start(self, message: Message) -> None:
        await message.answer(
            tg_html("Bot is running. Send text, photo, or voice — Codex will reply here."),
        )

    async def on_reset(self, message: Message) -> None:
        if message.chat is None:
            return
        existed = await self._sessions.reset(message.chat.id)
        await message.answer(tg_html("Session reset." if existed else "No active session."))

    async def on_new(self, message: Message) -> None:
        """Start a new Codex thread without tearing down the WS session."""
        if message.chat is None:
            return
        session = await self._sessions.get(message.chat.id)
        if session is None:
            await message.answer(tg_html("New thread will open with the next message."))
            return
        await session.client.start_new_thread()
        async with SessionLocal() as db:
            await event_service.emit(
                db,
                EventKind.THREAD_RESET,
                chat_id=session.db_chat_id,
                user_id=session.db_user_id,
            )
            await db.commit()
        await message.answer(tg_html("New thread started — context cleared."))

    async def on_stop(self, message: Message) -> None:
        if message.chat is None:
            return
        session = await self._sessions.get(message.chat.id)
        if session is None:
            await message.answer(tg_html("No active turn to stop."))
            return
        cancelled = await cancel_turn(session)
        await message.answer(
            tg_html("Turn interrupted." if cancelled else "No active turn to stop."),
        )

    async def on_callback(self, query: CallbackQuery) -> None:
        if query.message is None or query.message.chat is None:
            await query.answer()
            return
        chat_id = query.message.chat.id
        session = await self._sessions.get(chat_id)
        if session is None:
            await query.answer("No active session", show_alert=False)
            return
        if query.data == CB_TURN_STOP:
            cancelled = await cancel_turn(session)
            await query.answer("Зупинено" if cancelled else "Нема активного turn'а")
        elif query.data == CB_TURN_NEW:
            await session.client.start_new_thread()
            async with SessionLocal() as db:
                await event_service.emit(
                    db,
                    EventKind.THREAD_RESET,
                    chat_id=session.db_chat_id,
                    user_id=session.db_user_id,
                )
                await db.commit()
            await query.answer("Новий thread")
        elif query.data == CB_TURN_STEER:
            if session.current_turn_task is None:
                await query.answer("Нема активного turn'а")
                return
            session.steer_pending = True
            await query.answer("Напиши доповнення наступним повідомленням")
            await query.message.answer(
                tg_html("✏️ Напиши що додати — наступне повідомлення піде у поточний turn"),
            )
        else:
            await query.answer()

    async def on_incoming(self, message: Message) -> None:
        if message.chat is None:
            return
        log.info(
            "tg_incoming",
            chat_id=message.chat.id,
            message_id=message.message_id,
            from_user=message.from_user.id if message.from_user else None,
            has_text=bool(message.text),
            has_caption=bool(message.caption),
            has_photo=bool(message.photo),
            has_document=bool(message.document),
            has_voice=bool(message.voice),
            has_audio=bool(message.audio),
        )
        await self._runner.handle(message)
