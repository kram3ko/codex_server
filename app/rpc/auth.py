"""Connect handler для `codex.v1.AuthService`.

Email+password → JWT (Login/Register). JWT доставляється у HttpOnly cookie
(`codex_jwt`) — клієнт не зберігає у localStorage (CLAUDE.md §5). Refresh
видає свіжий cookie без перелогіну; Logout скидає cookie.
"""

from typing import override

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.config import settings
from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import auth_pb2
from app.grpc_generated.codex.v1.auth_connect import AuthService as AuthProtocol
from app.models import UserRole
from app.rpc._auth import jwt_subject
from app.services.auth import throttle
from app.services.auth.cookie import build_clear_cookie, build_set_cookie
from app.services.auth.default import auth_service
from app.services.invites.default import invite_service
from app.services.invites.service import InviteExpired, InviteNotFound, InviteUsed
from app.services.users.default import user_service

_email_adapter = TypeAdapter(EmailStr)


def _validated_email(raw: str) -> str:
    """Нормалізує + валідує email через `pydantic.EmailStr` (email-validator)."""
    try:
        return _email_adapter.validate_python(raw.strip().lower())
    except ValidationError as exc:
        raise ConnectError(Code.INVALID_ARGUMENT, "valid email required") from exc


def _client_ip(ctx: RequestContext) -> str | None:
    addr = ctx.client_address()
    if not addr:
        return None
    # connectrpc передає "ip:port" — беремо лише ip.
    return addr.rsplit(":", 1)[0] or addr


def _set_jwt_cookie(ctx: RequestContext, token: str, ttl_seconds: int) -> None:
    ctx.response_headers().add("Set-Cookie", build_set_cookie(token, ttl_seconds))


class AuthRPC(AuthProtocol):
    @override
    async def login(
        self,
        request: auth_pb2.LoginRequest,
        ctx: RequestContext,
    ) -> auth_pb2.LoginResponse:
        ip = _client_ip(ctx)
        try:
            await throttle.check(ip)
        except throttle.LoginThrottled as exc:
            raise ConnectError(Code.RESOURCE_EXHAUSTED, str(exc)) from exc

        email = request.email.strip().lower()
        async with SessionLocal() as db:
            user = await user_service.get_by_email(db, email)
            if user is None or not await auth_service.verify_password(
                request.password, user.password_hash
            ):
                await throttle.register_failure(ip)
                raise ConnectError(Code.UNAUTHENTICATED, "invalid email or password")
            if auth_service.needs_rehash(user.password_hash):
                await user_service.set_password_hash(
                    db, user, await auth_service.hash_password(request.password)
                )
                await db.commit()

        await throttle.clear(ip)
        access_token, expires_in = auth_service.issue_token(email)
        _set_jwt_cookie(ctx, access_token, expires_in)
        return auth_pb2.LoginResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=expires_in,
        )

    @override
    async def register(
        self,
        request: auth_pb2.RegisterRequest,
        ctx: RequestContext,
    ) -> auth_pb2.LoginResponse:
        ip = _client_ip(ctx)
        try:
            await throttle.check(ip)
        except throttle.LoginThrottled as exc:
            raise ConnectError(Code.RESOURCE_EXHAUSTED, str(exc)) from exc

        email = _validated_email(request.email)
        password = request.password
        if len(password) < 8:
            raise ConnectError(Code.INVALID_ARGUMENT, "password must be ≥ 8 chars")

        admin_bypass = email == settings.ADMIN_EMAIL.strip().lower() and bool(email)
        invite_token = request.invite_token.strip()
        if not admin_bypass and not invite_token:
            raise ConnectError(Code.INVALID_ARGUMENT, "invite_token required")

        display_name = request.display_name.strip() or None
        async with SessionLocal() as db:
            existing = await user_service.get_by_email(db, email)
            if existing is not None and existing.password_hash:
                await throttle.register_failure(ip)
                raise ConnectError(Code.ALREADY_EXISTS, "email already registered")

            user = existing or await user_service.get_or_create_by_email(
                db, email, display_name=display_name
            )
            if admin_bypass:
                user.role = UserRole.ADMIN
            else:
                try:
                    await invite_service.redeem(db, token=invite_token, user_id=user.id)
                except InviteNotFound as exc:
                    raise ConnectError(Code.NOT_FOUND, "invite not found") from exc
                except InviteUsed as exc:
                    raise ConnectError(Code.FAILED_PRECONDITION, "invite already used") from exc
                except InviteExpired as exc:
                    raise ConnectError(Code.FAILED_PRECONDITION, "invite expired") from exc
                user.role = UserRole.USER
            if display_name and not user.display_name:
                user.display_name = display_name
            await user_service.set_password_hash(
                db, user, await auth_service.hash_password(password)
            )
            await db.commit()

        await throttle.clear(ip)
        access_token, expires_in = auth_service.issue_token(email)
        _set_jwt_cookie(ctx, access_token, expires_in)
        return auth_pb2.LoginResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=expires_in,
        )

    @override
    async def refresh(
        self,
        request: auth_pb2.RefreshRequest,
        ctx: RequestContext,
    ) -> auth_pb2.LoginResponse:
        del request
        subject = jwt_subject(ctx)
        access_token, expires_in = auth_service.issue_token(subject)
        _set_jwt_cookie(ctx, access_token, expires_in)
        return auth_pb2.LoginResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=expires_in,
        )

    @override
    async def logout(
        self,
        request: auth_pb2.LogoutRequest,
        ctx: RequestContext,
    ) -> auth_pb2.LogoutResponse:
        del request
        ctx.response_headers().add("Set-Cookie", build_clear_cookie())
        return auth_pb2.LogoutResponse()
