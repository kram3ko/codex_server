"""JWT authentication helper for Connect-RPC handlers.

Pulls JWT з HttpOnly cookie `codex_jwt` (CLAUDE.md §5 — XSS-захист), валідує
через `auth_service`, resolves subject (email) до DB User. Use:

    user = await require_user(ctx)
"""

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.db.base import SessionLocal
from app.models import User, UserRole
from app.services.auth.cookie import read_jwt
from app.services.auth.default import auth_service
from app.services.auth.service import InvalidToken
from app.services.users.default import user_service


def jwt_subject(ctx: RequestContext) -> str:
    headers = ctx.request_headers()
    cookie_header = headers.get("cookie") or headers.get("Cookie")
    token = read_jwt(cookie_header)
    if token is None:
        raise ConnectError(Code.UNAUTHENTICATED, "missing session cookie")
    try:
        return auth_service.validate_token(token)
    except InvalidToken as exc:
        raise ConnectError(Code.UNAUTHENTICATED, str(exc)) from exc


async def require_user(ctx: RequestContext) -> User:
    """Validate JWT + return DB User by email-from-subject."""
    email = jwt_subject(ctx)
    async with SessionLocal() as db:
        user = await user_service.get_by_email(db, email)
        if user is None:
            raise ConnectError(Code.UNAUTHENTICATED, "user not found")
        return user


async def require_admin(ctx: RequestContext) -> User:
    """Same as require_user, але вимагає `role=ADMIN`. PERMISSION_DENIED інакше."""
    user = await require_user(ctx)
    if user.role != UserRole.ADMIN:
        raise ConnectError(Code.PERMISSION_DENIED, "admin role required")
    return user
