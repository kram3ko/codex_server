"""Per-thread identity для MCP-tool calls.

Codex app-server приймає у `thread/start` / `thread/resume` поле `config` з
override'ами конфігу, scoped до thread; серед них `mcp_servers.<name>.
http_headers`. Ми кладемо туди підписаний JWT з user/chat/sidecar — Codex
додає його до кожного HTTP-запиту цього thread у наш MCP-сервер, модель
заголовка не бачить. Тули читають claims із запиту, а не з аргументів.

Токен — лише ідентифікатор thread'а, не grant: роль не зашита, а
резолвиться з БД на кожен виклик (`resolve`), тож зняття прав чи видалення
юзера діє одразу, без відкликання. Admin-доступ потребує і ролі ADMIN у БД,
і admin-sidecar'а.

Без `exp`: заголовок фіксується на весь час, поки thread завантажений у
app-server (новий `config` на resume живого thread не застосовується), тож
expiry лише ламав би тули посеред довгого thread. Із цієї ж причини після
деплою сервера sidecar'и теж треба перезапустити: thread'и, завантажені до
того, заголовка не мають. Токен ніколи не виходить за docker-мережу і не
потрапляє у промпт. `Authorization` лишається connection-level bearer
sidecar'а — його per-thread override Codex ігнорує.
"""

import time
from typing import Any

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserRole
from app.services.codex.sidecar import SidecarName
from app.services.mcp_authz.schemas import (
    McpAuthzClaims,
    McpAuthzError,
    McpAuthzRole,
    McpIdentity,
)
from app.services.users.service import UserService

AUTHZ_HEADER = "X-Codex-Authz"
# Має збігатися з `[mcp_servers.codex_app]` у docker/codex/entrypoint.sh.
MCP_SERVER_NAME = "codex_app"

_ALGORITHM = "HS256"
_ISSUER = "codex-server"


class McpAuthzService:
    def __init__(self, secret: str, users: UserService) -> None:
        self._secret = secret
        self._users = users

    def issue(self, *, user_id: int, chat_id: int, sidecar: SidecarName) -> str:
        payload = {
            "iss": _ISSUER,
            "user_id": user_id,
            "chat_id": chat_id,
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

    async def resolve(self, session: AsyncSession, claims: McpAuthzClaims) -> McpIdentity:
        user = await self._users.get(session, claims.user_id)
        if user is None:
            raise McpAuthzError(f"unknown_user: {claims.user_id}")
        is_admin = user.role is UserRole.ADMIN and claims.sidecar is SidecarName.ADMIN
        return McpIdentity(
            user_id=user.id,
            chat_id=claims.chat_id,
            role=McpAuthzRole.ADMIN if is_admin else McpAuthzRole.USER,
        )

    def thread_config(self, token: str) -> dict[str, Any]:
        """`config` для thread/start|resume. Dotted key — deep-merge з базовим
        config.toml; одно-сегментний `mcp_servers` затер би решту серверів."""
        return {f"mcp_servers.{MCP_SERVER_NAME}": {"http_headers": {AUTHZ_HEADER: token}}}
