"""Thin aiogram handlers — delegate to TurnRunner / ChatSessionStore."""

from datetime import UTC, datetime

import structlog
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db.base import SessionLocal
from app.models import EventKind
from app.services.chats.default import chat_service
from app.services.codex.runner import open_codex_turn
from app.services.codex_usage.default import codex_usage_service
from app.services.codex_usage.service import CodexUsage, UsageWindow
from app.services.events.default import event_service
from app.tg.markdown import tg_markdown
from app.tg.progress import CB_TURN_STOP
from app.tg.sessions import ChatSessionStore, TGSessionKey
from app.tg.turn import TurnRunner, cancel_turn

log = structlog.get_logger(__name__)


def _topic_key(message: Message) -> TGSessionKey:
    # `is_topic_message` фільтрує не-форумні reply: у них теж є `message_thread_id`,
    # але вони мають лишатись в одному чаті групи, не плодити нові.
    thread_id = message.message_thread_id if message.is_topic_message else None
    return (message.chat.id, thread_id)


class TGHandlers:
    def __init__(self, sessions: ChatSessionStore, runner: TurnRunner) -> None:
        self._sessions = sessions
        self._runner = runner

    async def on_start(self, message: Message) -> None:
        await message.answer(
            tg_markdown.escape(
                "Bot is running. Send text, photo, or voice — Codex will reply here.",
            )
        )

    async def on_reset(self, message: Message) -> None:
        if message.chat is None:
            return
        existed = await self._sessions.reset(_topic_key(message))
        await message.answer(
            tg_markdown.escape(
                "Session reset." if existed else "No active session.",
            )
        )

    async def on_new(self, message: Message) -> None:
        # Очищає DB-кеш thread_id; наступний turn відкриє свіжий thread.
        if message.chat is None:
            return
        session = await self._sessions.get(_topic_key(message))
        if session is None:
            await message.answer(tg_markdown.escape("New thread will open with the next message."))
            return
        async with SessionLocal() as db:
            await chat_service.set_codex_thread_id(db, session.db_chat_id, None)
            await event_service.emit(
                db,
                EventKind.THREAD_RESET,
                chat_id=session.db_chat_id,
                user_id=session.db_user_id,
            )
            await db.commit()
        await message.answer(tg_markdown.escape("New thread will open with the next message."))

    async def on_stop(self, message: Message) -> None:
        # /stop кнопка/команда — interrupt running codex turn + cancel local task.
        if message.chat is None:
            return
        session = await self._sessions.get(_topic_key(message))
        if session is None:
            await message.answer(tg_markdown.escape("No active turn to stop."))
            return
        cancelled = await cancel_turn(session)
        await message.answer(
            tg_markdown.escape("Turn interrupted." if cancelled else "No active turn to stop."),
        )

    async def on_codex_usage(self, message: Message) -> None:
        # Admin-only — відкриває коротко-живу WS, читає rate-limits, закриває.
        if (
            message.from_user is None
            or message.chat is None
            or message.from_user.id not in settings.TG_ADMIN_USER_IDS
        ):
            return
        _, thread_id = _topic_key(message)
        session = await self._sessions.get_or_open(
            tg_user_id=message.from_user.id,
            tg_chat_id=message.chat.id,
            tg_message_thread_id=thread_id,
            display_name=message.from_user.full_name,
        )
        async with open_codex_turn(
            session.db_chat_id,
            user_id=session.db_user_id,
            is_admin=session.is_admin,
            seed_history=False,
        ) as client:
            usage = await codex_usage_service.latest(client)
        if usage is None:
            await message.answer(tg_markdown.escape("Sidecar не expose'ить rate-limits RPC."))
            return
        await message.answer(tg_markdown.escape(_format_codex_usage(usage)))

    async def on_callback(self, query: CallbackQuery) -> None:
        # Тільки Stop у inline. /new команда лишається окремо як `on_new`.
        # `query.message` може бути `InaccessibleMessage` (видалене) — там нема
        # ні `is_topic_message`, ні `message_thread_id`; такий callback не має
        # активної сесії за визначенням.
        if not isinstance(query.message, Message):
            await query.answer()
            return
        session = await self._sessions.get(_topic_key(query.message))
        if session is None:
            await query.answer("No active session", show_alert=False)
            return
        if query.data == CB_TURN_STOP:
            cancelled = await cancel_turn(session)
            await query.answer("Зупинено" if cancelled else "Нема активного turn'а")
        else:
            await query.answer()

    async def on_incoming(self, message: Message) -> None:
        if message.chat is None:
            return
        await self._runner.handle(message)


def _format_codex_usage(usage: CodexUsage) -> str:
    plan = usage.plan_type or "unknown"
    sections: list[list[str]] = []
    if usage.primary is not None:
        sections.append(_format_window("5h", usage.primary))
    if usage.secondary is not None:
        sections.append(_format_window("week", usage.secondary))
    blocks = [[f"📊 Codex usage · {plan}"], *sections]
    if usage.updated_at is not None:
        blocks.append([f"updated {_format_dt(usage.updated_at)}"])
    return "\n\n".join("\n".join(b) for b in blocks)


def _format_window(label: str, window: UsageWindow) -> list[str]:
    used = round(window.used_percent)
    left = round(window.left_percent)
    return [
        f"{label}   {_usage_dots(window.used_percent)} {used}% used",
        f"left {left}%",
        f"reset {_format_dt(window.resets_at)}",
    ]


def _usage_dots(used_percent: float, *, width: int = 10) -> str:
    filled = max(0, min(width, round(width * used_percent / 100)))
    icon = _usage_icon(used_percent)
    return icon * filled + "⚪" * (width - filled)


def _usage_icon(used_percent: float) -> str:
    if used_percent >= 80:
        return "🔴"
    if used_percent >= 50:
        return "🟡"
    return "🟢"


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
