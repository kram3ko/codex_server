"""AdminRPC — require_admin guard + invite/create_user flows."""

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from connectrpc.code import Code
from connectrpc.errors import ConnectError

from app.config import Settings
from app.grpc_generated.codex.v1 import admin_pb2
from app.models import Invite, User, UserRole
from app.rpc import _auth as auth_helper
from app.rpc import admin as admin_rpc
from app.services.auth.cookie import JWT_COOKIE_NAME
from app.services.auth.service import AuthService
from app.services.invites.service import InviteService
from app.services.users.service import UserService


class _Session:
    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def refresh(self, _obj: object) -> None:
        return None

    async def delete(self, _obj: object) -> None:
        return None

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _ResponseHeaders:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def add(self, key: str, value: str) -> None:
        self.items.append((key, value))


class _Ctx:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self._headers = headers or {}
        self.response = _ResponseHeaders()

    def request_headers(self) -> dict[str, str]:
        return self._headers

    def response_headers(self) -> _ResponseHeaders:
        return self.response

    def client_address(self) -> str | None:
        return None


def _auth_svc() -> AuthService:
    return AuthService(
        Settings(JWT_SECRET="test-secret-do-not-use-in-prod-32+chars", JWT_TTL_HOURS=1)
    )


def _cookie_ctx(token: str) -> _Ctx:
    return _Ctx({"cookie": f"{JWT_COOKIE_NAME}={token}"})


@contextlib.contextmanager
def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user: User | None,
    invite: Invite | None = None,
):
    """Підставляє singleton'и + SessionLocal у admin + _auth модулях."""
    svc = _auth_svc()

    class _Users(UserService):
        async def get_by_email(self, _session: Any, email: str) -> User | None:
            return user if (user and user.email == email) else None

        async def get_or_create_by_email(
            self, _session: Any, email: str, display_name: str | None = None
        ) -> User:
            return User(
                id=42,
                email=email,
                display_name=display_name,
                role=UserRole.USER,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

        async def set_password_hash(self, _session: Any, target: User, password_hash: str) -> None:
            target.password_hash = password_hash

        async def list_all(self, _session: Any) -> list[User]:
            return [user] if user else []

    class _Invites(InviteService):
        async def create(
            self,
            _session: Any,
            *,
            created_by_user_id: int,
            ttl_days: int = 0,
        ) -> Invite:
            del ttl_days
            return Invite(
                id=1,
                token="test-token",
                created_by_user_id=created_by_user_id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

        async def list_active(self, _session: Any) -> list[Invite]:
            return [invite] if invite else []

        async def list_all(self, _session: Any) -> list[Invite]:
            return [invite] if invite else []

        async def revoke(self, _session: Any, invite_id: int) -> bool:
            return invite is not None and invite.id == invite_id

    users = _Users()
    invites = _Invites()
    monkeypatch.setattr(admin_rpc, "auth_service", svc)
    monkeypatch.setattr(admin_rpc, "user_service", users)
    monkeypatch.setattr(admin_rpc, "invite_service", invites)
    monkeypatch.setattr(admin_rpc, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(auth_helper, "auth_service", svc)
    monkeypatch.setattr(auth_helper, "user_service", users)
    monkeypatch.setattr(auth_helper, "SessionLocal", lambda: _Session())
    yield svc


# --- require_admin guard ---


async def test_create_invite_rejects_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    user = User(id=10, email="user@example.com", role=UserRole.USER)
    with _wire(monkeypatch, user=user) as svc:
        token, _ = svc.issue_token(user.email)
        with pytest.raises(ConnectError) as exc:
            await admin_rpc.AdminRPC().create_invite(
                admin_pb2.CreateInviteRequest(ttl_days=7),
                _cookie_ctx(token),
            )
    assert exc.value.code is Code.PERMISSION_DENIED


async def test_create_invite_rejects_missing_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    with _wire(monkeypatch, user=None), pytest.raises(ConnectError) as exc:
        await admin_rpc.AdminRPC().create_invite(admin_pb2.CreateInviteRequest(ttl_days=7), _Ctx())
    assert exc.value.code is Code.UNAUTHENTICATED


# --- create_invite happy ---


async def test_create_invite_returns_token(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = User(id=1, email="admin@example.com", role=UserRole.ADMIN)
    with _wire(monkeypatch, user=admin) as svc:
        token, _ = svc.issue_token(admin.email)
        response = await admin_rpc.AdminRPC().create_invite(
            admin_pb2.CreateInviteRequest(ttl_days=7),
            _cookie_ctx(token),
        )
    assert response.invite.token == "test-token"
    assert response.invite.created_by_user_id == admin.id


# --- list_invites ---


async def test_list_invites_active_only(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = User(id=1, email="admin@example.com", role=UserRole.ADMIN)
    invite = Invite(
        id=5,
        token="abc",
        created_by_user_id=admin.id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with _wire(monkeypatch, user=admin, invite=invite) as svc:
        token, _ = svc.issue_token(admin.email)
        response = await admin_rpc.AdminRPC().list_invites(
            admin_pb2.ListInvitesRequest(include_used=False),
            _cookie_ctx(token),
        )
    assert len(response.invites) == 1
    assert response.invites[0].id == 5


# --- create_user validation ---


async def test_create_user_rejects_invalid_email(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = User(id=1, email="admin@example.com", role=UserRole.ADMIN)
    with _wire(monkeypatch, user=admin) as svc:
        token, _ = svc.issue_token(admin.email)
        with pytest.raises(ConnectError) as exc:
            await admin_rpc.AdminRPC().create_user(
                admin_pb2.CreateUserRequest(
                    email="not-an-email",
                    password="strongpass",
                    display_name="X",
                    role=admin_pb2.USER_ROLE_USER,
                ),
                _cookie_ctx(token),
            )
    assert exc.value.code is Code.INVALID_ARGUMENT


async def test_create_user_rejects_short_password(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = User(id=1, email="admin@example.com", role=UserRole.ADMIN)
    with _wire(monkeypatch, user=admin) as svc:
        token, _ = svc.issue_token(admin.email)
        with pytest.raises(ConnectError) as exc:
            await admin_rpc.AdminRPC().create_user(
                admin_pb2.CreateUserRequest(
                    email="new@example.com",
                    password="short",
                    display_name="X",
                    role=admin_pb2.USER_ROLE_USER,
                ),
                _cookie_ctx(token),
            )
    assert exc.value.code is Code.INVALID_ARGUMENT


async def test_create_user_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = User(id=1, email="admin@example.com", role=UserRole.ADMIN)
    with _wire(monkeypatch, user=admin) as svc:
        token, _ = svc.issue_token(admin.email)
        response = await admin_rpc.AdminRPC().create_user(
            admin_pb2.CreateUserRequest(
                email="new@example.com",
                password="strongpass123",
                display_name="New User",
                role=admin_pb2.USER_ROLE_USER,
            ),
            _cookie_ctx(token),
        )
    assert response.email == "new@example.com"
    assert response.display_name == "New User"
    assert response.role == admin_pb2.USER_ROLE_USER
