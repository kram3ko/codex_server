"""Telegram bot lifecycle: starts polling, owns dispatcher wiring."""

import asyncio
import contextlib
import os
from uuid import uuid4

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand

from app.config import settings
from app.services.cache.default import cache
from app.services.stt.default import stt_service
from app.tg.handlers import TGHandlers
from app.tg.sessions import ChatSessionStore
from app.tg.turn import TurnRunner

log = structlog.get_logger(__name__)

_TG_POLLING_LOCK_KEY = "tg:polling:lock"
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""
_RENEW_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""


class TGBotService:
    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._dispatcher: Dispatcher | None = None
        self._polling_task: asyncio.Task | None = None
        self._lock_renew_task: asyncio.Task | None = None
        self._lock_token: str | None = None
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

        if not await self._acquire_polling_lock():
            log.warning("tg_bot_skipped", reason="polling_lock_held")
            await bot.session.close()
            return

        self._bot = bot
        log.info("tg_bot_connected", username=me.username, id=me.id)
        with contextlib.suppress(Exception):
            await bot.set_my_commands([
                BotCommand(command="new", description="Новий thread (скинути контекст)"),
                BotCommand(command="stop", description="Зупинити поточну відповідь"),
                BotCommand(command="reset", description="Закрити сесію"),
                BotCommand(command="restart", description="Перезапустити сервер"),
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
        self._lock_renew_task = asyncio.create_task(
            self._renew_polling_lock(),
            name="tg_polling_lock_renew",
        )
        log.info(
            "tg_bot_started",
            allowed_user_ids=settings.TG_ALLOWED_USER_IDS,
            stt_enabled=self._stt.enabled,
        )

    async def stop(self) -> None:
        if self._lock_renew_task is not None:
            self._lock_renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._lock_renew_task
            self._lock_renew_task = None
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
        await self._release_polling_lock()

    def _build_dispatcher(self) -> Dispatcher:
        dispatcher = Dispatcher()
        allowed = settings.TG_ALLOWED_USER_IDS
        user_filter = F.from_user.id.in_(allowed) if allowed else F
        incoming_filter = F.photo | F.document | F.text | F.voice | F.audio
        dispatcher.message.register(self._handlers.on_start, CommandStart(), user_filter)
        dispatcher.message.register(self._handlers.on_new, Command("new"), user_filter)
        dispatcher.message.register(self._handlers.on_reset, Command("reset"), user_filter)
        dispatcher.message.register(self._handlers.on_stop, Command("stop"), user_filter)
        dispatcher.message.register(self._handlers.on_restart, Command("restart"), user_filter)
        dispatcher.message.register(self._handlers.on_incoming, user_filter, incoming_filter)
        dispatcher.callback_query.register(
            self._handlers.on_callback,
            F.from_user.id.in_(allowed) if allowed else F.data,
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

    async def _acquire_polling_lock(self) -> bool:
        token = f"{os.getpid()}:{uuid4()}"
        ttl = settings.TG_POLLING_LOCK_TTL_SECONDS
        try:
            acquired = await cache.set(_TG_POLLING_LOCK_KEY, token, nx=True, ex=ttl)
        except Exception as exc:  # noqa: BLE001
            log.error("tg_polling_lock_acquire_failed", error=str(exc))
            return False
        if not acquired:
            return False
        self._lock_token = token
        log.info("tg_polling_lock_acquired", ttl=ttl)
        return True

    async def _renew_polling_lock(self) -> None:
        ttl = settings.TG_POLLING_LOCK_TTL_SECONDS
        interval = max(1.0, ttl / 3)
        while True:
            await asyncio.sleep(interval)
            token = self._lock_token
            if token is None:
                return
            try:
                # redis-py overloads eval as sync|async — narrow for async client.
                renewed = await cache.eval(  # type: ignore[misc]
                    _RENEW_LOCK_SCRIPT, 1, _TG_POLLING_LOCK_KEY, token, ttl,
                )
                if not renewed:
                    log.error("tg_polling_lock_lost")
                    if self._dispatcher is not None:
                        with contextlib.suppress(Exception):
                            await self._dispatcher.stop_polling()
                    return
            except Exception as exc:  # noqa: BLE001
                log.error("tg_polling_lock_renew_failed", error=str(exc))
                if self._dispatcher is not None:
                    with contextlib.suppress(Exception):
                        await self._dispatcher.stop_polling()
                return

    async def _release_polling_lock(self) -> None:
        token = self._lock_token
        self._lock_token = None
        if token is None:
            return
        with contextlib.suppress(Exception):
            await cache.eval(  # type: ignore[misc]
                _RELEASE_LOCK_SCRIPT, 1, _TG_POLLING_LOCK_KEY, token,
            )
        log.info("tg_polling_lock_released")


tg_bot_service = TGBotService()
