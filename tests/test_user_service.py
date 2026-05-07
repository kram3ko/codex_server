"""UserService — admin role seed на create + idempotent ensure_admin_roles."""

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

    user = await UserService().get_or_create_by_tg(session, tg_user_id=12345)  # type: ignore[arg-type]

    assert user.role is UserRole.ADMIN
    assert user.tg_user_id == 12345
    assert session.added == [user]


async def test_get_or_create_by_tg_seeds_default_user_role_for_unlisted_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.users import service as service_module

    monkeypatch.setattr(service_module.settings, "TG_ADMIN_USER_IDS", {12345})
    session = _Session()

    user = await UserService().get_or_create_by_tg(session, tg_user_id=99999)  # type: ignore[arg-type]

    assert user.role is UserRole.USER


async def test_get_or_create_by_tg_does_not_demote_existing_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Існуючий ADMIN не повинен стати USER коли env очистили."""
    from app.services.users import service as service_module

    monkeypatch.setattr(service_module.settings, "TG_ADMIN_USER_IDS", set())
    existing = User(tg_user_id=12345, role=UserRole.ADMIN, display_name="me")
    session = _Session(existing=existing)

    user = await UserService().get_or_create_by_tg(session, tg_user_id=12345)  # type: ignore[arg-type]

    assert user is existing
    assert user.role is UserRole.ADMIN
    assert session.added == []  # nothing was inserted


async def test_ensure_admin_roles_no_op_when_env_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.users import service as service_module

    monkeypatch.setattr(service_module.settings, "TG_ADMIN_USER_IDS", set())
    session = _Session(update_rowcount=42)  # rowcount ignored, бо ми exit'имо раніше

    promoted = await UserService().ensure_admin_roles(session)  # type: ignore[arg-type]

    assert promoted == 0
    assert session.statements == []  # SQL не виконується коли admin set пустий


async def test_ensure_admin_roles_promotes_only_unpromoted_listed_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.users import service as service_module

    monkeypatch.setattr(service_module.settings, "TG_ADMIN_USER_IDS", {12345, 67890})
    session = _Session(update_rowcount=2)

    promoted = await UserService().ensure_admin_roles(session)  # type: ignore[arg-type]

    assert promoted == 2
    assert len(session.statements) == 1
    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ),
    )
    assert "UPDATE users SET role" in sql
    # WHERE filter: only USER role + tg_user_id у admin set
    assert "users.tg_user_id IN" in sql
    assert "users.role = 'USER'" in sql
