"""Thin aiogram handlers — delegate to TurnRunner / ChatSessionStore."""

from datetime import UTC, datetime

import structlog
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db.base import SessionLocal
from app.models import EventKind
from app.services.codex_usage.default import codex_usage_service
from app.services.codex_usage.service import CodexUsage, UsageWindow
from app.services.events.default import event_service
from app.tg.markdown import tg_markdown
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
            tg_markdown.escape(
                "Bot is running. Send text, photo, or voice — Codex will reply here.",
            )
        )

    async def on_reset(self, message: Message) -> None:
        if message.chat is None:
            return
        existed = await self._sessions.reset(message.chat.id)
        await message.answer(
            tg_markdown.escape(
                "Session reset." if existed else "No active session.",
            )
        )

    async def on_new(self, message: Message) -> None:
        """Start a new Codex thread without tearing down the WS session."""
        if message.chat is None:
            return
        session = await self._sessions.get(message.chat.id)
        if session is None:
            await message.answer(tg_markdown.escape("New thread will open with the next message."))
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
        await message.answer(tg_markdown.escape("New thread started — context cleared."))

    async def on_stop(self, message: Message) -> None:
        if message.chat is None:
            return
        session = await self._sessions.get(message.chat.id)
        if session is None:
            await message.answer(tg_markdown.escape("No active turn to stop."))
            return
        cancelled = await cancel_turn(session)
        await message.answer(
            tg_markdown.escape("Turn interrupted." if cancelled else "No active turn to stop."),
        )

    async def on_codex_usage(self, message: Message) -> None:
        if message.from_user is None or message.from_user.id not in settings.TG_ADMIN_USER_IDS:
            return
        if message.chat is None:
            return
        session = await self._sessions.get(message.chat.id)
        if session is None:
            await message.answer(
                tg_markdown.escape("Спочатку напиши боту хоч одне повідомлення."),
            )
            return
        usage = await codex_usage_service.latest(session.client)
        if usage is None:
            await message.answer(tg_markdown.escape("Sidecar не expose'ить rate-limits RPC."))
            return
        await message.answer(tg_markdown.escape(_format_codex_usage(usage)))

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
                tg_markdown.escape(
                    "✏️ Напиши що додати — наступне повідомлення піде у поточний turn",
                )
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
            has_video_note=bool(message.video_note),
        )
        await self._runner.handle(message)


def _format_codex_usage(usage: CodexUsage) -> str:
    plan = usage.plan_type or "unknown"
    lines = [f"📊 Codex usage · {plan}", ""]
    if usage.primary is not None:
        lines.extend(_format_window("5h", usage.primary))
    if usage.secondary is not None:
        if len(lines) > 2:
            lines.append("")
        lines.extend(_format_window("week", usage.secondary))
    if usage.updated_at is not None:
        lines.extend(("", f"updated {_format_dt(usage.updated_at)}"))
    return "\n".join(lines)


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
