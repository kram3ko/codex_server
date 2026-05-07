"""MCP sub-app, transport-layer bearer auth, error handlers, mount.

`mcp_app` — окремий FastAPI що монтується головним `app` на `/mcp`. Тут живе
лише транспорт + auth; кожна тула декорується у своєму файлі під
`app/mcp/tools/` і реєструється side-effect import'ом у `tools/__init__.py`.

Auth — Bearer токен (`MCP_CALLBACK_TOKEN`). Codex CLI sidecar шле його
verbatim у Authorization. Токен живе у docker-network перетині, тому це
друга лінія захисту, перша — мережева ізоляція.
"""

import secrets

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi_mcp import FastApiMCP

from app.config import settings
from app.mcp.errors import ToolError

log = structlog.get_logger(__name__)

mcp_app = FastAPI(
    title="Codex Server MCP Tools",
    description="Tools exposed to Codex CLI via streamable-HTTP MCP transport",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@mcp_app.middleware("http")
async def mcp_auth_middleware(request: Request, call_next):
    """Validate Bearer token on every MCP request. Always required."""
    expected = settings.MCP_CALLBACK_TOKEN
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": "Bearer token required", "code": "missing_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[7:]
    if not secrets.compare_digest(token, expected):
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid bearer token", "code": "auth_error"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


@mcp_app.exception_handler(ToolError)
async def _tool_error_handler(_request: Request, exc: ToolError) -> JSONResponse:
    log.warning("mcp_tool_error", code=exc.code, error=str(exc))
    return JSONResponse(
        status_code=422,
        content={"error": str(exc), "code": exc.code},
    )


def mount_mcp_server() -> None:
    """Атачить streamable-HTTP MCP transport на `mcp_app` у `/streamable`.

    Викликати ПІСЛЯ side-effect import'у тул, інакше fastapi-mcp не побачить
    зареєстровані ендпоінти. Підсумкова URL для Codex CLI:
    `http://codex-server:8000/mcp/streamable`.
    Кидає RuntimeError якщо `MCP_CALLBACK_TOKEN` порожній — fail-closed.
    """
    if not settings.MCP_CALLBACK_TOKEN:
        raise RuntimeError(
            "MCP_CALLBACK_TOKEN is empty — refusing to mount MCP. "
            "Generate з `openssl rand -hex 32` і пропиши у .env.",
        )
    mcp = FastApiMCP(
        mcp_app,
        name="Codex Server Tools",
        description="Show user-uploaded media, lookup chat artifacts.",
    )
    mcp.mount_http(mount_path="/streamable")
    log.info("mcp_server_mounted", transport="streamable_http", path="/mcp/streamable")
