"""AuthRPC + require_user — login/refresh contract + Bearer handling."""

import contextlib
from typing import Any

import pytest
from connectrpc.code import Code
from connectrpc.errors import ConnectError

from app.config import Settings
from app.grpc_generated.codex.v1 import auth_pb2
from app.models import User
from app.rpc import _auth as auth_helper
from app.rpc import auth as auth_rpc
from app.services.auth.service import AuthService
from app.services.users.service import UserService

# --- shared fakes -----------------------------------------------------------


class _Session:
    """Stub AsyncSession, just enough для commit/flush у RPC хендлерах."""

    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Ctx:
    """Fake connectrpc RequestContext — повертає підкинуті headers."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self._headers = headers or {}

    def request_headers(self) -> dict[str, str]:
        return self._headers


def _make_auth_service() -> AuthService:
    cfg = Settings(
        JWT_SECRET="test-secret-do-not-use-in-prod",
        JWT_ALGORITHM="HS256",
        JWT_TTL_HOURS=1,
    )
    return AuthService(cfg)


@contextlib.contextmanager
def _wire(monkeypatch: pytest.MonkeyPatch, *, user: User | None, auth_service: AuthService):
    """Підставляє singleton'и + SessionLocal у обох auth-модулях (RPC + helper)."""
    captured: dict[str, Any] = {"hash_set_to": None}

    class _Users(UserService):
        async def get_by_email(self, session: Any, email: str) -> User | None:  # type: ignore[override]
            captured["last_email_lookup"] = email
            return user

        async def set_password_hash(
            self,
            session: Any,
            target: User,
            password_hash: str,
        ) -> None:  # type: ignore[override]
            captured["hash_set_to"] = password_hash
            target.password_hash = password_hash

    users = _Users()

    monkeypatch.setattr(auth_rpc, "auth_service", auth_service)
    monkeypatch.setattr(auth_rpc, "user_service", users)
    monkeypatch.setattr(auth_rpc, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(auth_helper, "auth_service", auth_service)
    monkeypatch.setattr(auth_helper, "user_service", users)
    monkeypatch.setattr(auth_helper, "SessionLocal", lambda: _Session())
    yield captured


# --- login ------------------------------------------------------------------


async def test_login_happy_path_returns_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _make_auth_service()
    user = User(email="user@example.com", password_hash=svc.hash_password("secret"))
    with _wire(monkeypatch, user=user, auth_service=svc):
        response = await auth_rpc.AuthRPC().login(
            auth_pb2.LoginRequest(email="user@example.com", password="secret"),
            _Ctx(),  # type: ignore[arg-type]
        )
    assert response.token_type == "Bearer"
    assert response.expires_in == 3600
    assert svc.validate_token(response.access_token) == "user@example.com"


async def test_login_normalizes_email(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _make_auth_service()
    user = User(email="user@example.com", password_hash=svc.hash_password("secret"))
    with _wire(monkeypatch, user=user, auth_service=svc) as cap:
        await auth_rpc.AuthRPC().login(
            auth_pb2.LoginRequest(email="  USER@Example.COM  ", password="secret"),
            _Ctx(),  # type: ignore[arg-type]
        )
    assert cap["last_email_lookup"] == "user@example.com"


async def test_login_rejects_unknown_email(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _make_auth_service()
    with _wire(monkeypatch, user=None, auth_service=svc), pytest.raises(ConnectError) as exc:
        await auth_rpc.AuthRPC().login(
            auth_pb2.LoginRequest(email="ghost@example.com", password="x"),
            _Ctx(),  # type: ignore[arg-type]
        )
    assert exc.value.code is Code.UNAUTHENTICATED


async def test_login_rejects_wrong_password(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _make_auth_service()
    user = User(email="user@example.com", password_hash=svc.hash_password("real"))
    with _wire(monkeypatch, user=user, auth_service=svc), pytest.raises(ConnectError) as exc:
        await auth_rpc.AuthRPC().login(
            auth_pb2.LoginRequest(email="user@example.com", password="wrong"),
            _Ctx(),  # type: ignore[arg-type]
        )
    assert exc.value.code is Code.UNAUTHENTICATED


async def test_login_rejects_user_without_password_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TG-only юзер (password_hash=None) не може залогінитись паролем."""
    svc = _make_auth_service()
    user = User(email="tg@example.com", password_hash=None)
    with _wire(monkeypatch, user=user, auth_service=svc), pytest.raises(ConnectError) as exc:
        await auth_rpc.AuthRPC().login(
            auth_pb2.LoginRequest(email="tg@example.com", password="x"),
            _Ctx(),  # type: ignore[arg-type]
        )
    assert exc.value.code is Code.UNAUTHENTICATED


# --- refresh ----------------------------------------------------------------


async def test_refresh_issues_new_token_for_valid_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _make_auth_service()
    token, _ = svc.issue_token("user@example.com")
    with _wire(monkeypatch, user=None, auth_service=svc):
        response = await auth_rpc.AuthRPC().refresh(
            auth_pb2.RefreshRequest(),
            _Ctx({"authorization": f"Bearer {token}"}),  # type: ignore[arg-type]
        )
    assert svc.validate_token(response.access_token) == "user@example.com"


async def test_refresh_rejects_missing_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _make_auth_service()
    with _wire(monkeypatch, user=None, auth_service=svc), pytest.raises(ConnectError) as exc:
        await auth_rpc.AuthRPC().refresh(
            auth_pb2.RefreshRequest(),
            _Ctx(),  # type: ignore[arg-type]
        )
    assert exc.value.code is Code.UNAUTHENTICATED


async def test_refresh_rejects_invalid_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _make_auth_service()
    with _wire(monkeypatch, user=None, auth_service=svc), pytest.raises(ConnectError) as exc:
        await auth_rpc.AuthRPC().refresh(
            auth_pb2.RefreshRequest(),
            _Ctx({"authorization": "Bearer not.a.jwt"}),  # type: ignore[arg-type]
        )
    assert exc.value.code is Code.UNAUTHENTICATED


# --- require_user -----------------------------------------------------------


async def test_require_user_returns_user_from_jwt_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _make_auth_service()
    token, _ = svc.issue_token("user@example.com")
    user = User(email="user@example.com")
    with _wire(monkeypatch, user=user, auth_service=svc):
        result = await auth_helper.require_user(
            _Ctx({"authorization": f"Bearer {token}"}),  # type: ignore[arg-type]
        )
    assert result is user


async def test_require_user_rejects_unknown_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _make_auth_service()
    token, _ = svc.issue_token("ghost@example.com")
    with _wire(monkeypatch, user=None, auth_service=svc), pytest.raises(ConnectError) as exc:
        await auth_helper.require_user(
            _Ctx({"authorization": f"Bearer {token}"}),  # type: ignore[arg-type]
        )
    assert exc.value.code is Code.UNAUTHENTICATED


async def test_require_user_rejects_missing_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _make_auth_service()
    with _wire(monkeypatch, user=None, auth_service=svc), pytest.raises(ConnectError) as exc:
        await auth_helper.require_user(_Ctx())  # type: ignore[arg-type]
    assert exc.value.code is Code.UNAUTHENTICATED
