"""Адмін-керовані runtime-ліміти: singleton-строка `runtime_settings` (id=1).

Читається на кожний turn через короткий in-process кеш (TTL), тож зміни з
адмінки доїжджають без рестарту server/worker. Міграція сідить строку з
дефолтами, тож відсутність строки — лише у свіжих тестових схемах.
"""

import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import RuntimeSetting
from app.models.runtime_setting import SINGLETON_ID
from app.services.runtime_settings.schemas import RuntimeSettings


class RuntimeSettingsService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], ttl_s: float) -> None:
        self._session_factory = session_factory
        self._ttl_s = ttl_s
        self._cached: RuntimeSettings | None = None
        self._expires_at = 0.0

    async def get(self) -> RuntimeSettings:
        if self._cached is not None and time.monotonic() < self._expires_at:
            return self._cached
        async with self._session_factory() as db:
            current = await self.load(db)
        self.remember(current)
        return current

    async def load(self, db: AsyncSession) -> RuntimeSettings:
        row = await db.get(RuntimeSetting, SINGLETON_ID)
        return RuntimeSettings.from_row(row) if row is not None else RuntimeSettings()

    async def save(self, db: AsyncSession, data: RuntimeSettings) -> RuntimeSettings:
        """Пише у строку без touch кешу — caller після commit кличе `remember`."""
        row = await db.get(RuntimeSetting, SINGLETON_ID, with_for_update=True)
        if row is None:
            row = RuntimeSetting(id=SINGLETON_ID)
            db.add(row)
        data.apply_to(row)
        await db.flush()
        return data

    def invalidate(self) -> None:
        self._cached = None
        self._expires_at = 0.0

    def remember(self, value: RuntimeSettings) -> None:
        self._cached = value
        self._expires_at = time.monotonic() + self._ttl_s
