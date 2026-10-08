"""runtime_settings — TTL-кеш, mapping DTO ↔ row, резолв лімітів по kind."""

from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.models import RuntimeSetting, User, UserRole
from app.services.runtime_settings.schemas import (
    MB,
    RuntimeSettings,
    TurnLimits,
    UserKind,
    user_kind,
)
from app.services.runtime_settings.service import RuntimeSettingsService


class _FakeSession:
    def __init__(self, row: RuntimeSetting | None) -> None:
        self.row = row
        self.added: list[Any] = []
        self.gets = 0

    async def get(self, _model: type, _pk: int, **_: Any) -> RuntimeSetting | None:
        self.gets += 1
        return self.row

    def add(self, row: Any) -> None:
        self.added.append(row)
        self.row = row

    async def flush(self) -> None:
        return None


def _factory(session: _FakeSession):
    @asynccontextmanager
    async def _open():
        yield session

    return _open


def test_user_kind_resolution() -> None:
    assert user_kind(User(id=1, role=UserRole.ADMIN)) is UserKind.ADMIN
    assert user_kind(User(id=2, email="a@b.c", role=UserRole.USER)) is UserKind.WEB_USER
    assert user_kind(User(id=3, tg_user_id=5, role=UserRole.USER)) is UserKind.TG_GUEST


def test_turn_limits_admin_unlimited() -> None:
    limits = RuntimeSettings().turn_limits(UserKind.ADMIN)
    assert limits == TurnLimits(active=0, hourly=0)


def test_max_upload_bytes_only_for_tg_guest() -> None:
    value = RuntimeSettings(tg_guest_max_upload_mb=5)
    assert value.max_upload_bytes(UserKind.TG_GUEST) == 5 * MB
    assert value.max_upload_bytes(UserKind.WEB_USER) is None
    assert RuntimeSettings(tg_guest_max_upload_mb=0).max_upload_bytes(UserKind.TG_GUEST) is None


def test_row_roundtrip() -> None:
    row = RuntimeSetting(id=1)
    RuntimeSettings(web_user=TurnLimits(active=4, hourly=50), tg_guest_max_upload_mb=7).apply_to(
        row
    )
    assert RuntimeSettings.from_row(row) == RuntimeSettings(
        web_user=TurnLimits(active=4, hourly=50), tg_guest_max_upload_mb=7
    )


def test_rejects_upload_above_bot_api_ceiling() -> None:
    with pytest.raises(ValueError):
        RuntimeSettings(tg_guest_max_upload_mb=21)


async def test_get_caches_within_ttl() -> None:
    session = _FakeSession(None)
    service = RuntimeSettingsService(_factory(session), ttl_s=60)
    first = await service.get()
    second = await service.get()
    assert first == second == RuntimeSettings()
    assert session.gets == 1


async def test_get_reloads_after_ttl_expires() -> None:
    session = _FakeSession(None)
    service = RuntimeSettingsService(_factory(session), ttl_s=0)
    await service.get()
    await service.get()
    assert session.gets == 2


async def test_save_creates_singleton_without_touching_cache() -> None:
    """Кеш оновлюється лише після commit (`remember`), не на flush."""
    session = _FakeSession(None)
    service = RuntimeSettingsService(_factory(session), ttl_s=60)
    await service.get()
    data = RuntimeSettings(tg_guest=TurnLimits(active=1, hourly=10))
    await service.save(session, data)
    assert session.added and session.added[0].id == 1
    assert session.added[0].tg_guest_hourly == 10
    assert await service.get() == RuntimeSettings()
    service.remember(data)
    assert await service.get() == data
