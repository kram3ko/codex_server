"""Auth-залежності для FastAPI (HTTP/WS) і Connect-RPC."""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.auth_service import InvalidToken, auth_service

_bearer = HTTPBearer(auto_error=False)


async def require_user_http(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """FastAPI dependency для HTTP — Authorization: Bearer <jwt>."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        return auth_service.validate_token(creds.credentials)
    except InvalidToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


async def require_user_ws(
    websocket: WebSocket,
    token: Annotated[str | None, Query()] = None,
) -> str:
    """Браузерні WS не підтримують custom headers — auth через ?token=..."""
    if not token:
        await websocket.close(code=4401, reason="missing token")
        raise RuntimeError("ws closed: no token")
    try:
        return auth_service.validate_token(token)
    except InvalidToken as exc:
        await websocket.close(code=4401, reason=str(exc))
        raise RuntimeError(f"ws closed: {exc}") from exc
