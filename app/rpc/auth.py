"""Connect handler для `codex.v1.AuthService`.

Email+password → JWT (Login). Bearer → новий JWT (Refresh). Тонкий —
бізнес-логіка живе в `AuthService` + `UserService` (singletons з default).
"""

from typing import override

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import auth_pb2
from app.grpc_generated.codex.v1.auth_connect import AuthService as AuthProtocol
from app.rpc._auth import jwt_subject
from app.services.auth.default import auth_service
from app.services.users.default import user_service


class AuthRPC(AuthProtocol):
    @override
    async def login(
        self,
        request: auth_pb2.LoginRequest,
        ctx: RequestContext,
    ) -> auth_pb2.LoginResponse:
        del ctx
        email = request.email.strip().lower()
        async with SessionLocal() as db:
            user = await user_service.get_by_email(db, email)
            if user is None or not auth_service.verify_password(
                request.password, user.password_hash
            ):
                raise ConnectError(Code.UNAUTHENTICATED, "invalid email or password")
            if auth_service.needs_rehash(user.password_hash):
                await user_service.set_password_hash(
                    db, user, auth_service.hash_password(request.password)
                )
                await db.commit()

        access_token, expires_in = auth_service.issue_token(email)
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
        return auth_pb2.LoginResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=expires_in,
        )
