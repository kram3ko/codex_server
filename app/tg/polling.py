"""Polling fallback для local/dev. Removable файл — webhook у `service.py`.

Telegram забороняє >1 активних polling-клієнтів на токен (409 Conflict).
закMulti-worker координація через Redis-lock з TTL: active renew, standby
нескінченно пробує acquire. На втраті lock active стопить polling і
переходить у standby — будь-який worker підбере lock і продовжить.
"""

import asyncio
import contextlib
import os
from collections.abc import Awaitable
from typing import Any, cast
from uuid import uuid4

import structlog
from aiogram import Bot, Dispatcher
from redis.exceptions import RedisError

from app.services.cache.default import cache

log = structlog.get_logger(__name__)

_LOCK_KEY = "tg:polling:lock"
_LOCK_TTL_S = 60
_RENEW_INTERVAL_S = _LOCK_TTL_S / 3

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""
_RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""


class PollingMode:
    def __init__(self, bot: Bot, dispatcher: Dispatcher) -> None:
        self._bot = bot
        self._dispatcher = dispatcher
        self._lock_token: str | None = None
        self._polling_task: asyncio.Task | None = None
        self._lock_renew_task: asyncio.Task | None = None
        self._lock_wait_task: asyncio.Task | None = None

    @property
    def is_owner(self) -> bool:
        return self._lock_token is not None

    async def start(self) -> None:
        if await self._acquire_lock():
            self._launch()
            return
        log.info("tg_polling_lock_busy_waiting")
        self._lock_wait_task = asyncio.create_task(self._wait_for_lock(), name="tg_polling_wait")

    async def stop(self) -> None:
        for task in (self._lock_wait_task, self._lock_renew_task, self._polling_task):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._lock_wait_task = None
        self._lock_renew_task = None
        self._polling_task = None
        if self._dispatcher is not None:
            with contextlib.suppress(Exception):
                await self._dispatcher.stop_polling()
        await self._release_lock()

    async def _wait_for_lock(self) -> None:
        attempt = 0
        while True:
            attempt += 1
            await asyncio.sleep(_RENEW_INTERVAL_S)
            if await self._acquire_lock():
                log.info("tg_polling_promoted", attempt=attempt)
                self._launch()
                return

    def _launch(self) -> None:
        self._polling_task = asyncio.create_task(
            self._dispatcher.start_polling(
                self._bot, handle_signals=False, drop_pending_updates=True
            ),
            name="tg_polling",
        )
        self._polling_task.add_done_callback(self._on_polling_done)
        self._lock_renew_task = asyncio.create_task(self._renew_lock(), name="tg_polling_renew")
        log.debug("tg_polling_started")

    @staticmethod
    def _on_polling_done(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("tg_polling_failed")

    async def _acquire_lock(self) -> bool:
        token = f"{os.getpid()}:{uuid4()}"
        try:
            acquired = await cache.set(_LOCK_KEY, token, nx=True, ex=_LOCK_TTL_S)
        except RedisError:
            log.exception("tg_polling_lock_acquire_failed")
            return False
        if not acquired:
            return False
        self._lock_token = token
        log.debug("tg_polling_lock_acquired", ttl=_LOCK_TTL_S)
        return True

    async def _renew_lock(self) -> None:
        while True:
            await asyncio.sleep(_RENEW_INTERVAL_S)
            token = self._lock_token
            if token is None:
                return
            try:
                renewed = await cast(
                    "Awaitable[Any]",
                    cache.eval(_RENEW_SCRIPT, 1, _LOCK_KEY, token, _LOCK_TTL_S),
                )
            except RedisError:
                log.exception("tg_polling_lock_renew_failed")
                await self._handle_lock_lost()
                return
            if not renewed:
                log.error("tg_polling_lock_lost")
                await self._handle_lock_lost()
                return

    async def _handle_lock_lost(self) -> None:
        self._lock_token = None
        with contextlib.suppress(Exception):
            await self._dispatcher.stop_polling()
        self._polling_task = None
        self._lock_renew_task = None
        self._lock_wait_task = asyncio.create_task(self._wait_for_lock(), name="tg_polling_wait")

    async def _release_lock(self) -> None:
        token = self._lock_token
        self._lock_token = None
        if token is None:
            return
        with contextlib.suppress(Exception):
            await cast(
                "Awaitable[Any]",
                cache.eval(_RELEASE_SCRIPT, 1, _LOCK_KEY, token),
            )
        log.debug("tg_polling_lock_released")
