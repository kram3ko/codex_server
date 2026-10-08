"""UserService — admin role seed на create + idempotent sync_admin_roles."""

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update

from app.models import User, UserRole
from app.services.users.service import UserService


class _NoneScalars:
    def scalar_one_or_none(self) -> None:
        return None


class _ExistingScalars:
    def __init__(self, user: User) -> None:
        self._user = user

    def scalar_one_or_none(self) -> User:
        return self._user


class _UpdateResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Session:
    """Тонкий fake AsyncSession — ловимо statement + emit'имо canned результат."""

    def __init__(self, *, existing: User | None = None, update_rowcount: int = 0) -> None:
        self._existing = existing
        self._update_rowcount = update_rowcount
        self.added: list[User] = []
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        if isinstance(statement, Update):
            return _UpdateResult(self._update_rowcount)
        return _ExistingScalars(self._existing) if self._existing else _NoneScalars()

    def add(self, obj: User) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


async def test_get_or_create_by_tg_seeds_admin_role_when_id_in_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.users import service as service_module

    monkeypatch.setattr(service_module.settings, "TG_ADMIN_USER_IDS", {12345})
    session = _Session()

    user = await UserService().get_or_create_by_tg(session, tg_user_id=12345)

    assert user.role is UserRole.ADMIN
    assert user.tg_user_id == 12345
    assert session.added == [user]


async def test_get_or_create_by_tg_seeds_default_user_role_for_unlisted_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.users import service as service_module

    monkeypatch.setattr(service_module.settings, "TG_ADMIN_USER_IDS", {12345})
    session = _Session()

    user = await UserService().get_or_create_by_tg(session, tg_user_id=99999)

    assert user.role is UserRole.USER


async def test_get_or_create_by_tg_does_not_demote_existing_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Існуючий ADMIN не повинен стати USER коли env очистили."""
    from app.services.users import service as service_module

    monkeypatch.setattr(service_module.settings, "TG_ADMIN_USER_IDS", set())
    existing = User(tg_user_id=12345, role=UserRole.ADMIN, display_name="me")
    session = _Session(existing=existing)

    user = await UserService().get_or_create_by_tg(session, tg_user_id=12345)

    assert user is existing
    assert user.role is UserRole.ADMIN
    assert session.added == []  # nothing was inserted


def _compiled(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ),
    )


async def test_sync_admin_roles_empty_list_only_demotes() -> None:
    """Порожній список: promote не виконується, TG-only ADMIN-и демотяться."""
    session = _Session(update_rowcount=3)

    promoted, demoted = await UserService().sync_admin_roles(session, set())

    assert (promoted, demoted) == (0, 3)
    assert len(session.statements) == 1
    sql = _compiled(session.statements[0])
    assert "SET role='USER'" in sql
    assert "users.email IS NULL" in sql


async def test_sync_admin_roles_promotes_listed_and_demotes_unlisted() -> None:
    session = _Session(update_rowcount=2)

    promoted, demoted = await UserService().sync_admin_roles(session, {12345, 67890})

    assert (promoted, demoted) == (2, 2)
    assert len(session.statements) == 2
    promote_sql, demote_sql = (_compiled(st) for st in session.statements)
    assert "SET role='ADMIN'" in promote_sql
    assert "users.role = 'USER'" in promote_sql
    assert "SET role='USER'" in demote_sql
    assert "NOT IN" in demote_sql
    assert "users.email IS NULL" in demote_sql
