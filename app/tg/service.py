"""Telegram bot lifecycle. Webhook (prod) or polling fallback (local/dev).

Toggle через `TG_WEBHOOK_URL`: задано → webhook, інакше → polling
(`app/tg/polling.py`, видаляється коли публічний URL доступний).
"""

import contextlib

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, BotCommandScopeChat, Update

from app.config import settings
from app.services.stt.default import stt_service
from app.tg.handlers import TGHandlers
from app.tg.polling import PollingMode
from app.tg.sessions import ChatSessionStore
from app.tg.turn import TurnRunner

log = structlog.get_logger(__name__)


class TGBotService:
    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._dispatcher: Dispatcher | None = None
        self._polling: PollingMode | None = None
        self._mode: str = "off"
        self._sessions = ChatSessionStore()
        self._stt = stt_service
        self._runner = TurnRunner(self._sessions, self._stt)
        self._handlers = TGHandlers(self._sessions, self._runner)

    @property
    def bot(self) -> Bot | None:
        return self._bot

    @property
    def webhook_secret(self) -> str:
        return settings.TG_WEBHOOK_SECRET

    @property
    def mode(self) -> str:
        return self._mode

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
        except TelegramAPIError:
            log.exception("tg_bot_auth_failed")
            await bot.session.close()
            return

        self._bot = bot
        self._dispatcher = self._build_dispatcher()
        log.info("tg_bot_connected", username=me.username, id=me.id)
        await self._register_bot_commands()

        if settings.TG_WEBHOOK_URL and settings.TG_WEBHOOK_SECRET:
            await self._start_webhook()
        else:
            await self._start_polling()

    async def feed_update(self, update: Update) -> None:
        if self._mode != "webhook" or self._bot is None or self._dispatcher is None:
            log.warning("tg_update_dropped", reason="bot_not_in_webhook_mode", mode=self._mode)
            return
        await self._dispatcher.feed_update(self._bot, update)

    async def interrupt_active_turns(self) -> int:
        return await self._sessions.interrupt_all_turns()

    async def stop(self) -> None:
        if self._mode == "webhook" and self._bot is not None:
            with contextlib.suppress(Exception):
                await self._bot.delete_webhook(drop_pending_updates=False)
        if self._polling is not None:
            await self._polling.stop()
            self._polling = None
        if self._bot is not None:
            with contextlib.suppress(Exception):
                await self._bot.session.close()
            self._bot = None
        self._dispatcher = None
        self._mode = "off"

    async def _start_webhook(self) -> None:
        assert self._bot is not None and self._dispatcher is not None
        try:
            await self._bot.set_webhook(
                url=settings.TG_WEBHOOK_URL,
                secret_token=settings.TG_WEBHOOK_SECRET,
                allowed_updates=self._dispatcher.resolve_used_update_types(),
                drop_pending_updates=True,
            )
        except TelegramAPIError:
            log.exception("tg_webhook_register_failed", url=settings.TG_WEBHOOK_URL)
            with contextlib.suppress(Exception):
                await self._bot.session.close()
            self._bot = None
            self._dispatcher = None
            return
        self._mode = "webhook"
        log.info(
            "tg_bot_started",
            mode="webhook",
            url=settings.TG_WEBHOOK_URL,
            admin_user_ids=settings.TG_ADMIN_USER_IDS,
            stt_enabled=self._stt.enabled,
        )

    async def _start_polling(self) -> None:
        assert self._bot is not None and self._dispatcher is not None
        # Drop any leftover webhook config so Telegram переключиться на polling.
        with contextlib.suppress(Exception):
            await self._bot.delete_webhook(drop_pending_updates=False)
        self._polling = PollingMode(self._bot, self._dispatcher)
        await self._polling.start()
        self._mode = "polling"
        log.info(
            "tg_bot_started",
            mode=self._mode,
            owner=self._polling.is_owner,
            admin_user_ids=settings.TG_ADMIN_USER_IDS,
            stt_enabled=self._stt.enabled,
        )

    async def _register_bot_commands(self) -> None:
        assert self._bot is not None
        with contextlib.suppress(Exception):
            base_commands = [
                BotCommand(command="new", description="Новий thread (скинути контекст)"),
                BotCommand(command="stop", description="Зупинити поточну відповідь"),
                BotCommand(command="reset", description="Закрити сесію"),
            ]
            await self._bot.set_my_commands(base_commands)
            admin_commands = [
                *base_commands,
                BotCommand(command="codex_usage", description="Codex ліміти"),
            ]
            for admin_id in settings.TG_ADMIN_USER_IDS:
                await self._bot.set_my_commands(
                    admin_commands,
                    scope=BotCommandScopeChat(chat_id=admin_id),
                )

    def _build_dispatcher(self) -> Dispatcher:
        dispatcher = Dispatcher()
        incoming_filter = F.photo | F.document | F.text | F.voice | F.audio | F.video_note
        dispatcher.message.register(self._handlers.on_start, CommandStart())
        dispatcher.message.register(self._handlers.on_new, Command("new"))
        dispatcher.message.register(self._handlers.on_reset, Command("reset"))
        dispatcher.message.register(self._handlers.on_stop, Command("stop"))
        dispatcher.message.register(self._handlers.on_codex_usage, Command("codex_usage"))
        dispatcher.message.register(self._handlers.on_incoming, incoming_filter)
        dispatcher.callback_query.register(self._handlers.on_callback, F.data)
        return dispatcher


tg_bot_service = TGBotService()
