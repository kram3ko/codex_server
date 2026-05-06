"""Telegram bot lifecycle: starts polling, owns dispatcher wiring."""

import asyncio
import contextlib

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand

from app.config import settings
from app.services.stt.default import stt_service
from app.tg.handlers import TGHandlers
from app.tg.sessions import ChatSessionStore
from app.tg.turn import TurnRunner

log = structlog.get_logger(__name__)


class TGBotService:
    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._dispatcher: Dispatcher | None = None
        self._polling_task: asyncio.Task | None = None
        self._sessions = ChatSessionStore()
        self._stt = stt_service
        self._runner = TurnRunner(self._sessions, self._stt)
        self._handlers = TGHandlers(self._sessions, self._runner)

    async def start(self) -> None:
        if not settings.TG_BOT_TOKEN or settings.TG_BOT_TOKEN.startswith("123456:"):
            log.info("tg_bot_skipped", reason="token_not_set")
            return

        bot = Bot(
            token=settings.TG_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            me = await bot.get_me()
        except Exception as exc:  # noqa: BLE001
            log.error("tg_bot_auth_failed", error=str(exc))
            await bot.session.close()
            return

        self._bot = bot
        log.info("tg_bot_connected", username=me.username, id=me.id)
        with contextlib.suppress(Exception):
            await bot.set_my_commands([
                BotCommand(command="new", description="Новий thread (скинути контекст)"),
                BotCommand(command="stop", description="Зупинити поточну відповідь"),
                BotCommand(command="reset", description="Закрити сесію"),
            ])

        dispatcher = self._build_dispatcher()
        self._dispatcher = dispatcher
        self._polling_task = asyncio.create_task(
            dispatcher.start_polling(
                bot,
                handle_signals=False,
                drop_pending_updates=True,
            ),
            name="tg_polling",
        )
        self._polling_task.add_done_callback(self._on_polling_done)
        log.info(
            "tg_bot_started",
            allowed_user=settings.TG_ALLOWED_USER_ID,
            stt_enabled=self._stt.enabled,
        )

    async def stop(self) -> None:
        if self._dispatcher is not None:
            with contextlib.suppress(Exception):
                await self._dispatcher.stop_polling()
        if self._polling_task is not None:
            self._polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._polling_task
            self._polling_task = None
        if self._bot is not None:
            with contextlib.suppress(Exception):
                await self._bot.session.close()
            self._bot = None
        self._dispatcher = None
        await self._sessions.close_all()

    def _build_dispatcher(self) -> Dispatcher:
        dispatcher = Dispatcher()
        allowed = settings.TG_ALLOWED_USER_ID
        user_filter = F.from_user.id == allowed if allowed is not None else F
        incoming_filter = F.photo | F.document | F.text | F.voice | F.audio
        dispatcher.message.register(self._handlers.on_start, CommandStart(), user_filter)
        dispatcher.message.register(self._handlers.on_new, Command("new"), user_filter)
        dispatcher.message.register(self._handlers.on_reset, Command("reset"), user_filter)
        dispatcher.message.register(self._handlers.on_stop, Command("stop"), user_filter)
        dispatcher.message.register(self._handlers.on_incoming, user_filter, incoming_filter)
        dispatcher.callback_query.register(
            self._handlers.on_callback,
            F.from_user.id == allowed if allowed is not None else F.data,
        )
        return dispatcher

    @staticmethod
    def _on_polling_done(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.error("tg_polling_failed", error=str(exc))


tg_bot_service = TGBotService()
