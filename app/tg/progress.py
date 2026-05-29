"""Progress indication for Telegram turns.

Assistant text is streamed by editing one Telegram message, not by sending
many small bubbles.
"""

import asyncio
import contextlib
import time
from dataclasses import dataclass
from enum import StrEnum

import structlog
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.tg.markdown import tg_markdown

log = structlog.get_logger(__name__)

CB_TURN_STOP = "turn:stop"


class _ToolStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


_STATUS_ICONS: dict[_ToolStatus, str] = {
    _ToolStatus.RUNNING: "🔧",
    _ToolStatus.DONE: "✓",
    _ToolStatus.ERROR: "✗",
}


_DRAFT_TEXT_MAX = 4000
_STREAM_BUBBLE_MAX = 3500
_STREAM_THROTTLE_S = 1.0


@dataclass(slots=True)
class _ToolEntry:
    name: str
    status: _ToolStatus = _ToolStatus.RUNNING


def _turn_controls() -> InlineKeyboardMarkup:
    # Тільки Stop — Steer робить auto-steer на наступне повідомлення,
    # New-thread був дублюючим (юзер може почати з /new коли треба).
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⏹ Зупинити", callback_data=CB_TURN_STOP)]]
    )


class TurnProgressReporter:
    def __init__(
        self,
        originator_message: Message,
        *,
        delay_s: float | None = None,
        tick_s: float = 5.0,
        chat_action_period_s: float = 4.0,
        draft_throttle_s: float = 0.25,
        draft_enabled: bool | None = None,
    ) -> None:
        self._message = originator_message
        self._delay_s = settings.TG_PROGRESS_DELAY_SECONDS if delay_s is None else delay_s
        self._tick_s = tick_s
        self._chat_action_period_s = chat_action_period_s
        self._draft_throttle_s = draft_throttle_s
        self._draft_enabled = settings.TG_DRAFT_ENABLED if draft_enabled is None else draft_enabled
        self._draft_id = originator_message.message_id
        self._tools: list[_ToolEntry] = []
        self._last_draft_text: str = ""
        self._last_draft_at: float = 0.0
        self._status_message: Message | None = None
        self._last_status_text: str = ""
        self._stream_message: Message | None = None
        self._stream_text: str = ""
        self._stream_throttle_at: float = 0.0
        self._status_paused_until: float = 0.0
        self._draft_lock = asyncio.Lock()
        self._status_lock = asyncio.Lock()
        self._stream_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="tg_turn_progress")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self._status_message is None:
            return
        # Видаляємо статус-повідомлення повністю — фінальний "✓ Завершено" не
        # несе нової інфи (відповідь уже в потоці) і шумить у чаті.
        with contextlib.suppress(Exception):
            await self._status_message.delete()
        self._status_message = None

    async def note_tool(self, name: str) -> None:
        self._tools.append(_ToolEntry(name=name))
        await self.refresh_status()
        await self._render_draft(force=True)

    async def mark_tool_done(self, name: str, *, error: bool = False) -> None:
        for entry in reversed(self._tools):
            if entry.name == name and entry.status == _ToolStatus.RUNNING:
                entry.status = _ToolStatus.ERROR if error else _ToolStatus.DONE
                break
        await self.refresh_status()
        await self._render_draft(force=True)

    async def note_partial(self, full_text: str) -> None:
        """Mirror the current assistant buffer into one editable TG message."""
        if not full_text or tg_markdown.has_unclosed_fence(full_text):
            return
        if self._message.bot is None or self._message.chat is None:
            return
        async with self._stream_lock:
            now = time.monotonic()
            if now - self._stream_throttle_at < _STREAM_THROTTLE_S:
                return
            visible = _stream_window(full_text).strip()
            if not visible or visible == self._stream_text:
                return
            if tg_markdown.has_unclosed_fence(visible):
                return
            try:
                await self._upsert_stream_message(visible)
            except TelegramAPIError as exc:
                log.warning("tg_stream_edit_failed", error=str(exc))
                return
            self._stream_text = visible
            self._stream_throttle_at = now

    async def finalize_stream(self, final_text: str) -> str:
        """Render final first chunk into the editable message and return it."""
        if not final_text or self._message.bot is None or self._message.chat is None:
            return ""
        async with self._stream_lock:
            visible = _stream_window(final_text).strip()
            if not visible:
                return ""
            if tg_markdown.has_unclosed_fence(visible):
                await self._delete_stream_message()
                return ""
            try:
                await self._upsert_stream_message(visible)
            except TelegramAPIError as exc:
                log.warning("tg_stream_finalize_failed", error=str(exc))
                return ""
            self._stream_text = visible
            return visible

    async def _upsert_stream_message(self, text: str) -> None:
        chunks = tg_markdown.render_html(text)
        if not chunks:
            return
        rendered = chunks[0]
        thread_id = self._message.message_thread_id if self._message.is_topic_message else None
        if self._stream_message is None:
            self._stream_message = await self._message.answer(
                rendered,
                message_thread_id=thread_id,
            )
            return
        await self._stream_message.edit_text(rendered)

    async def _delete_stream_message(self) -> None:
        if self._stream_message is None:
            return
        with contextlib.suppress(TelegramAPIError):
            await self._stream_message.delete()
        self._stream_message = None
        self._stream_text = ""

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self._delay_s)
            bot = self._message.bot
            if bot is None or self._message.chat is None:
                return
            last_chat_action_at = 0.0
            while True:
                await self.refresh_status()
                now = time.monotonic()
                if now - last_chat_action_at >= self._chat_action_period_s:
                    thread_id = (
                        self._message.message_thread_id if self._message.is_topic_message else None
                    )
                    with contextlib.suppress(Exception):
                        await bot.send_chat_action(
                            chat_id=self._message.chat.id,
                            action=ChatAction.TYPING,
                            message_thread_id=thread_id,
                        )
                    last_chat_action_at = now
                await asyncio.sleep(self._tick_s)
        except asyncio.CancelledError:
            raise

    async def refresh_status(self) -> None:
        text = self.compose_status_text()
        if text == self._last_status_text:
            return
        now = time.monotonic()
        if now < self._status_paused_until:
            return
        rendered = tg_markdown.escape(text)
        async with self._status_lock:
            try:
                if self._status_message is None:
                    self._status_message = await self._message.answer(
                        rendered,
                        reply_markup=_turn_controls(),
                    )
                else:
                    await self._status_message.edit_text(
                        rendered,
                        reply_markup=_turn_controls(),
                    )
            except TelegramRetryAfter as exc:
                self._status_paused_until = time.monotonic() + exc.retry_after
                log.warning("tg_status_flood", retry_after=exc.retry_after)
                return
            except TelegramAPIError as exc:
                log.warning("tg_status_failed", error=str(exc))
                return
        self._last_status_text = text

    def compose_status_text(self) -> str:
        active = next((e for e in self._tools if e.status == _ToolStatus.RUNNING), None)
        if active is not None:
            return f"{_STATUS_ICONS[active.status]} {active.name}"
        return "⏳ Thinking…"

    async def _render_draft(self, *, force: bool = False) -> None:
        if not self._draft_enabled:
            return
        text = self._compose_draft_text()
        if not text:
            return
        now = time.monotonic()
        if not force and now - self._last_draft_at < self._draft_throttle_s:
            return
        if text == self._last_draft_text:
            return
        bot = self._message.bot
        if bot is None or self._message.chat is None:
            return
        async with self._draft_lock:
            try:
                thread_id = (
                    self._message.message_thread_id if self._message.is_topic_message else None
                )
                await bot.send_message_draft(
                    chat_id=self._message.chat.id,
                    draft_id=self._draft_id,
                    text=text[-_DRAFT_TEXT_MAX:],
                    message_thread_id=thread_id,
                )
            except TelegramAPIError as exc:
                log.warning("tg_draft_failed", error=str(exc))
                return
        self._last_draft_text = text
        self._last_draft_at = now

    def _compose_draft_text(self) -> str:
        if self._tools:
            return "\n".join(f"{_STATUS_ICONS[entry.status]} {entry.name}" for entry in self._tools)
        return ""


def _stream_window(text: str) -> str:
    if len(text) <= _STREAM_BUBBLE_MAX:
        return text
    window = text[:_STREAM_BUBBLE_MAX]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = window.rfind(sep)
        if idx > 0:
            return text[: idx + len(sep)]
    return text[:_STREAM_BUBBLE_MAX]
