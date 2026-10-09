"""Per-thread identity для MCP-tool calls.

Codex app-server приймає у `thread/start` / `thread/resume` поле `config` з
override'ами конфігу, scoped до thread; серед них `mcp_servers.<name>.
http_headers`. Ми кладемо туди підписаний JWT з user/chat/role — Codex додає
його до кожного HTTP-запиту цього thread у наш MCP-сервер, модель заголовка
не бачить. Тули читають claims із запиту, а не з аргументів.

Без `exp`: заголовок фіксується на весь час, поки thread завантажений у
app-server (перевірено: новий `config` на resume живого thread не
застосовується), тож expiry лише ламав би тули посеред довгого thread.
Токен ніколи не виходить за docker-мережу і не потрапляє у промпт.
`Authorization` при цьому лишається connection-level bearer sidecar'а —
його per-thread override Codex ігнорує, тому identity їде окремим заголовком.
"""

import time
from typing import Any

import jwt

from app.services.codex.sidecar import SidecarName
from app.services.mcp_authz.schemas import McpAuthzClaims, McpAuthzError, McpAuthzRole

AUTHZ_HEADER = "X-Codex-Authz"
# Має збігатися з `[mcp_servers.codex_app]` у docker/codex/entrypoint.sh.
MCP_SERVER_NAME = "codex_app"

_ALGORITHM = "HS256"
_ISSUER = "codex-server"


class McpAuthzService:
    def __init__(self, secret: str) -> None:
        self._secret = secret

    def issue(self, *, user_id: int, chat_id: int, sidecar: SidecarName) -> str:
        role = McpAuthzRole.ADMIN if sidecar is SidecarName.ADMIN else McpAuthzRole.USER
        payload = {
            "iss": _ISSUER,
            "user_id": user_id,
            "chat_id": chat_id,
            "role": role.value,
            "sidecar": sidecar.value,
            "iat": int(time.time()),
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def verify(self, token: str) -> McpAuthzClaims:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[_ALGORITHM], issuer=_ISSUER)
        except jwt.InvalidTokenError as exc:
            raise McpAuthzError(f"invalid_authz: {exc}") from exc
        # `iss` — стандартний claim; решту `extra="forbid"` відхиляє як injected.
        payload.pop("iss", None)
        try:
            return McpAuthzClaims.model_validate(payload)
        except ValueError as exc:
            raise McpAuthzError(f"malformed_claims: {exc}") from exc

    def thread_config(self, token: str) -> dict[str, Any]:
        """`config` для thread/start|resume. Dotted key — deep-merge з базовим
        config.toml; одно-сегментний `mcp_servers` затер би решту серверів."""
        return {f"mcp_servers.{MCP_SERVER_NAME}": {"http_headers": {AUTHZ_HEADER: token}}}
