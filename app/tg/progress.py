"""Progress indication helpers for Telegram turns."""

import asyncio
import contextlib
import time

from aiogram.enums import ChatAction
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.tg.formatting import tg_html

# Callback data prefixes — handled у `app/tg/handlers.py:on_callback`.
CB_TURN_STOP = "turn:stop"
CB_TURN_NEW = "turn:new"
CB_TURN_STEER = "turn:steer"


def _turn_controls() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏸ Зупинити", callback_data=CB_TURN_STOP),
            InlineKeyboardButton(text="💬 Дописати", callback_data=CB_TURN_STEER),
        ],
        [InlineKeyboardButton(text="🆕 Новий thread", callback_data=CB_TURN_NEW)],
    ])


class TurnProgressReporter:
    def __init__(self, message: Message, *, delay_s: float = 1.2, tick_s: float = 4.0) -> None:
        self._message = message
        self._delay_s = delay_s
        self._tick_s = tick_s
        self._started_at = time.monotonic()
        self._status_message: Message | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="tg_turn_progress")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self._status_message is not None:
            with contextlib.suppress(Exception):
                await self._status_message.delete()
            self._status_message = None

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self._delay_s)
            bot = self._message.bot
            if self._message.chat is None or bot is None:
                return
            while True:
                elapsed = int(time.monotonic() - self._started_at)
                await self._ensure_status_message(elapsed)
                await bot.send_chat_action(
                    chat_id=self._message.chat.id,
                    action=ChatAction.TYPING,
                )
                await asyncio.sleep(self._tick_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self._message.answer(tg_html(f"Progress error: {exc}"))

    async def _ensure_status_message(self, elapsed_s: int) -> None:
        text = tg_html(f"Thinking... {elapsed_s}s")
        if self._status_message is None:
            self._status_message = await self._message.answer(
                text, reply_markup=_turn_controls(),
            )
            return
        try:
            await self._status_message.edit_text(text, reply_markup=_turn_controls())
        except Exception:  # noqa: BLE001
            return
