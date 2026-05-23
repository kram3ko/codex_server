"""Per-turn JWT для MCP-tool authz.

Stateless verify (signature) замість Redis-lookup. Token живе у промпт-header
один turn (TTL 30хв), модель форвардить його як `authz` arg у tool body,
кожен tool робить `verify_authz(token)` → `McpAuthzClaims` без network calls.

Розділення на ролі: admin sidecar → `role='admin'` (full access у tools),
guest sidecar → `role='user'` (tools мусять scope-ити query по `user_id`/
`chat_id`). Tool без user-context (`show_image`) — verify лише signature.
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.codex.sidecar import SidecarName

_ALGORITHM = "HS256"
_ISSUER = "codex-server"


class McpAuthzRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class McpAuthzError(Exception):
    """Token signature/expiry/payload invalid."""


class McpAuthzClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: int = Field(description="DB users.id який ініціював turn.")
    chat_id: int = Field(description="DB chats.id для якого turn запущено.")
    role: McpAuthzRole = Field(description="`admin` → full access; `user` → scoped.")
    sidecar: SidecarName = Field(description="Який Codex sidecar обслуговує turn.")
    iat: int = Field(description="Issued-at unix-timestamp.")
    exp: int = Field(description="Expires-at unix-timestamp.")


def mint_authz(
    *,
    user_id: int,
    chat_id: int,
    role: McpAuthzRole,
    sidecar: SidecarName,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": _ISSUER,
        "user_id": user_id,
        "chat_id": chat_id,
        "role": role.value,
        "sidecar": sidecar.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.MCP_AUTHZ_TTL_S)).timestamp()),
    }
    return jwt.encode(payload, settings.MCP_AUTHZ_SECRET, algorithm=_ALGORITHM)


def verify_authz(token: str) -> McpAuthzClaims:
    try:
        payload = jwt.decode(
            token,
            settings.MCP_AUTHZ_SECRET,
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
        )
    except jwt.InvalidTokenError as exc:
        raise McpAuthzError(f"invalid_authz: {exc}") from exc
    # JWT-standard claims (`iss`) — забрати перед model_validate бо
    # `extra="forbid"` має відхиляти ЛИШЕ attacker-injected claims
    # (`admin: true` тощо).
    payload.pop("iss", None)
    try:
        return McpAuthzClaims.model_validate(payload)
    except ValueError as exc:
        raise McpAuthzError(f"malformed_claims: {exc}") from exc


def authz_prompt_header(token: str) -> str:
    """Формат який модель бачить у промпт-тексті і форвардить у tool body.

    Один рядок, easy-to-grep, no ambiguity для модельного parser-а.
    """
    return f"MCPAuthz: {token}"


def inject_authz(text: str, *, user_id: int, chat_id: int, sidecar: SidecarName) -> str:
    """Prepend a fresh JWT-header line до user text.

    Викликається ПІСЛЯ persist USER message — БД зберігає clean text, тільки
    payload що йде у Codex несе токен. Role derives зі sidecar: admin sidecar
    обслуговує лише owner, guest sidecar — TG-users.

    User content sanitize-иться (стріпаються рядки що починаються з `---`) —
    щоб атакуючий не міг "закрити" блок prompt-header і ін'єктнути свою
    інструкцію через crafted input.
    """
    role = McpAuthzRole.ADMIN if sidecar is SidecarName.ADMIN else McpAuthzRole.USER
    token = mint_authz(user_id=user_id, chat_id=chat_id, role=role, sidecar=sidecar)
    return f"{authz_prompt_header(token)}\n\n{_sanitize_user_text(text)}"


_DIVIDER_PREFIXES = ("---", "***", "===")
_HEADER_PREFIX = "mcpauthz:"


def _sanitize_user_text(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.lower().startswith(_HEADER_PREFIX):
            continue
        if any(stripped.startswith(p) for p in _DIVIDER_PREFIXES):
            continue
        out.append(line)
    return "\n".join(out)
