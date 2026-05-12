"""Progress indication for Telegram turns.

Assistant text is intentionally not mirrored into the draft bubble — some
clients render it as a duplicate final message.
"""

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Literal

import structlog
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.tg.markdown import tg_markdown

log = structlog.get_logger(__name__)

CB_TURN_STOP = "turn:stop"
CB_TURN_NEW = "turn:new"
CB_TURN_STEER = "turn:steer"


_ToolStatus = Literal["running", "done", "error"]
_STATUS_ICONS: dict[_ToolStatus, str] = {"running": "🔧", "done": "✓", "error": "✗"}

_TurnOutcome = Literal["success", "failed", "interrupted"]
_OUTCOME_ICON: dict[_TurnOutcome, str] = {"success": "✓", "failed": "✗", "interrupted": "⏸"}
_OUTCOME_LABEL: dict[_TurnOutcome, str] = {
    "success": "Завершено",
    "failed": "Помилка",
    "interrupted": "Зупинено",
}

_DRAFT_TEXT_MAX = 4000
_PROGRESS_BAR_WIDTH = 12

# Multi-bubble streaming: чим більший chunk — тим менше edit'ів, тим менше
# ratelimit-ризику. ~180 char-chunks з ≥1.2s паузою = ~50/min worst case,
# Telegram per-chat send limit ≈ 30/min, тому throttle 1.2s — захист.
_STREAM_MIN_CHARS = 180
_STREAM_BUBBLE_MAX = 3500
_STREAM_THROTTLE_S = 1.2


@dataclass(slots=True)
class _ToolEntry:
    name: str
    status: _ToolStatus = "running"


def _turn_controls() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏸ Зупинити", callback_data=CB_TURN_STOP),
                InlineKeyboardButton(text="💬 Дописати", callback_data=CB_TURN_STEER),
            ],
            [InlineKeyboardButton(text="🆕 Новий thread", callback_data=CB_TURN_NEW)],
        ]
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
        self._started_at = time.monotonic()
        self._tools: list[_ToolEntry] = []
        self._last_draft_text: str = ""
        self._last_draft_at: float = 0.0
        self._status_message: Message | None = None
        self._last_status_text: str = ""
        self._outcome: _TurnOutcome = "success"
        self._committed_text: str = ""
        self._stream_throttle_at: float = 0.0
        self._status_paused_until: float = 0.0
        self._draft_lock = asyncio.Lock()
        self._status_lock = asyncio.Lock()
        self._stream_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @property
    def committed_text(self) -> str:
        """Text already published as separate bubbles via `note_partial`."""
        return self._committed_text

    def mark_outcome(self, outcome: _TurnOutcome) -> None:
        """Caller signals final state; reflected у stop()'s edit."""
        self._outcome = outcome

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
        text = self._compose_status_text(done=True)
        # Skip edit якщо текст той самий — Telegram повертає `400 message
        # not modified` і ми отримаємо марний log.warning.
        if text != self._last_status_text:
            with contextlib.suppress(Exception):
                # No keyboard on the final state — turn is over, controls стали б no-op.
                await self._status_message.edit_text(tg_markdown.escape(text), reply_markup=None)
        self._status_message = None

    async def note_tool(self, name: str) -> None:
        self._tools.append(_ToolEntry(name=name))
        await self._refresh_status()
        await self._render_draft(force=True)

    async def mark_tool_done(self, name: str, *, error: bool = False) -> None:
        for entry in reversed(self._tools):
            if entry.name == name and entry.status == "running":
                entry.status = "error" if error else "done"
                break
        await self._refresh_status()
        await self._render_draft(force=True)

    async def note_partial(self, full_text: str) -> None:
        """Multi-bubble streaming: commit `full_text` tail as standalone TG
        bubbles when it grows past the threshold and throttle window passes.

        Caller stores the cumulative buffer; we publish only the un-emitted
        tail и tracking `_committed_text` so `TurnRunner` can deduplicate
        when the final answer arrives.
        """
        if not full_text or tg_markdown.has_unclosed_fence(full_text):
            return
        if self._message.bot is None or self._message.chat is None:
            return
        async with self._stream_lock:
            now = time.monotonic()
            if now - self._stream_throttle_at < _STREAM_THROTTLE_S:
                return
            tail = full_text[len(self._committed_text) :]
            if len(tail) < _STREAM_MIN_CHARS:
                return
            cut = _find_stream_split(tail)
            if cut is None:
                return
            raw_chunk = tail[:cut]
            visible = raw_chunk.strip()
            if not visible:
                return
            try:
                for piece in tg_markdown.render_html(visible):
                    await self._message.answer(piece)
            except TelegramAPIError as exc:
                log.warning("tg_stream_chunk_failed", error=str(exc))
                return
            self._committed_text += raw_chunk
            self._stream_throttle_at = now

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self._delay_s)
            bot = self._message.bot
            if bot is None or self._message.chat is None:
                return
            last_chat_action_at = 0.0
            while True:
                await self._refresh_status()
                now = time.monotonic()
                if now - last_chat_action_at >= self._chat_action_period_s:
                    with contextlib.suppress(Exception):
                        await bot.send_chat_action(
                            chat_id=self._message.chat.id,
                            action=ChatAction.TYPING,
                        )
                    last_chat_action_at = now
                await asyncio.sleep(self._tick_s)
        except asyncio.CancelledError:
            raise

    async def _refresh_status(self) -> None:
        text = self._compose_status_text()
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

    def _compose_status_text(self, *, done: bool = False) -> str:
        elapsed = int(time.monotonic() - self._started_at)
        if done:
            icon = _OUTCOME_ICON[self._outcome]
            label = _OUTCOME_LABEL[self._outcome]
        else:
            icon = "⏳"
            label = "Thinking…"
        lines = [f"{icon} {label} {elapsed}s", _progress_bar(elapsed, done=done)]
        lines.extend(f"{_STATUS_ICONS[entry.status]} {entry.name}" for entry in self._tools)
        return "\n".join(lines)

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
                await bot.send_message_draft(
                    chat_id=self._message.chat.id,
                    draft_id=self._draft_id,
                    text=text[-_DRAFT_TEXT_MAX:],
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


def _find_stream_split(tail: str) -> int | None:
    """Pick a natural break inside the first `_STREAM_BUBBLE_MAX` chars.

    None → defer: тейл < bubble max і нема break — чекаємо ще дельт, інакше
    порвемо слово типу `wait_agent` яке Codex стрімить по частинах.
    """
    window = tail[:_STREAM_BUBBLE_MAX]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = window.rfind(sep)
        if idx >= _STREAM_MIN_CHARS:
            return idx + len(sep)
    if len(tail) <= _STREAM_BUBBLE_MAX:
        return None
    return _STREAM_BUBBLE_MAX


def _progress_bar(elapsed: int, *, done: bool = False) -> str:
    if done:
        return "▓" * _PROGRESS_BAR_WIDTH
    width = _PROGRESS_BAR_WIDTH
    window = 4
    start = elapsed % width
    cells = []
    for index in range(width):
        active = (index - start) % width < window
        cells.append("▓" if active else "░")
    return "".join(cells)
