"""Caller identity для MCP-тулів — з HTTP-заголовка поточного запиту."""

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers

from app.db.base import SessionLocal
from app.services.mcp_authz.default import mcp_authz_service
from app.services.mcp_authz.schemas import McpAuthzClaims, McpAuthzError, McpIdentity
from app.services.mcp_authz.service import AUTHZ_HEADER


def require_claims() -> McpAuthzClaims:
    headers = get_http_headers(include={AUTHZ_HEADER.lower()})
    token = next((v for k, v in headers.items() if k.lower() == AUTHZ_HEADER.lower()), None)
    if not token:
        raise ToolError(f"{AUTHZ_HEADER} header missing: tool called outside a Codex thread")
    try:
        return mcp_authz_service.verify(token)
    except McpAuthzError as exc:
        raise ToolError(str(exc)) from exc


async def require_identity() -> McpIdentity:
    claims = require_claims()
    try:
        async with SessionLocal() as db:
            return await mcp_authz_service.resolve(db, claims)
    except McpAuthzError as exc:
        raise ToolError(str(exc)) from exc
