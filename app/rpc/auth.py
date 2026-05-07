"""Connect handler для `codex.v1.AuthService.Login`.

Тонкий — обгортає `AuthService` (бізнес-логіка) у Connect-RPC interface.
"""

from typing import override

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.grpc_generated.codex.v1 import auth_pb2
from app.grpc_generated.codex.v1.auth_connect import AuthService as AuthProtocol
from app.services.auth.service import AuthService, InvalidCredentials


class AuthRPC(AuthProtocol):
    """Implements connectrpc-generated `AuthService` Protocol."""

    def __init__(self, service: AuthService) -> None:
        self._service = service

    @override
    async def login(
        self,
        request: auth_pb2.LoginRequest,
        ctx: RequestContext,
    ) -> auth_pb2.LoginResponse:
        del ctx
        try:
            access_token, expires_in = self._service.issue_token(request.token)
        except InvalidCredentials as exc:
            raise ConnectError(Code.UNAUTHENTICATED, str(exc)) from exc

        return auth_pb2.LoginResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=expires_in,
        )
