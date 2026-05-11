"""JWT authentication helper for Connect-RPC handlers.

Pulls `Authorization: Bearer <jwt>` from `RequestContext.request_headers`,
validates через `auth_service`, resolves subject (email) до DB User. Use:

    user = await require_user(ctx)
"""

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.db.base import SessionLocal
from app.models import User
from app.services.auth.default import auth_service
from app.services.auth.service import InvalidToken
from app.services.users.default import user_service


def jwt_subject(ctx: RequestContext) -> str:
    headers = ctx.request_headers()
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise ConnectError(Code.UNAUTHENTICATED, "missing bearer token")
    try:
        return auth_service.validate_token(auth[7:].strip())
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
