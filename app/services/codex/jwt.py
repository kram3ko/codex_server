"""WS-handshake JWT для Codex app-server.

Codex CLI 0.131+ відмовляється listen на non-loopback WS без auth.
Sidecar (`codex app-server --ws-auth signed-bearer-token`) валідує issuer +
audience claim + HS256 підпис на handshake-у; після підняття WS — token уже
не перевіряється. TTL короткий (handshake-only, не per-message).

Per-audience secrets: admin і guest sidecar мають різні shared secrets — leak
одного не дає доступ до іншого.
"""

import time

import jwt as pyjwt

from app.config import settings
from app.services.codex.sidecar import SidecarName

_TOKEN_TTL_S = 60
_ALGORITHM = "HS256"


def make_ws_token(sidecar: SidecarName) -> str:
    if sidecar is SidecarName.ADMIN:
        secret = settings.CODEX_WS_SECRET_ADMIN
        audience = settings.CODEX_WS_AUDIENCE_ADMIN
    else:
        secret = settings.CODEX_WS_SECRET_GUEST
        audience = settings.CODEX_WS_AUDIENCE_GUEST
    now = int(time.time())
    payload = {
        "iss": settings.CODEX_WS_ISSUER,
        "aud": audience,
        "iat": now,
        "exp": now + _TOKEN_TTL_S,
    }
    return pyjwt.encode(payload, secret, algorithm=_ALGORITHM)
