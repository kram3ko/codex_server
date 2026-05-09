"""FastMCP: stateless streamable-HTTP, static bearer auth.

stateless_http=True — без session-id, працює під багатьма воркерами.
Тули — у `app/mcp/tools/`, декоруються `@mcp.tool` з flat-параметрами.
"""

import structlog
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from app.config import settings

log = structlog.get_logger(__name__)

if not settings.MCP_CALLBACK_TOKEN:
    raise RuntimeError(
        "MCP_CALLBACK_TOKEN is empty — refusing to start MCP. "
        "Generate з `openssl rand -hex 32` і пропиши у .env.",
    )

mcp: FastMCP = FastMCP(
    name="Codex Server Tools",
    instructions="Show user-uploaded media, lookup chat artifacts.",
    auth=StaticTokenVerifier(
        tokens={
            settings.MCP_CALLBACK_TOKEN: {
                "client_id": "codex-cli-sidecar",
                "scopes": [],
            },
        },
    ),
)


def build_mcp_http_app():
    """Build streamable-HTTP ASGI app. Викликати ПІСЛЯ import тул.

    Lifespan МУСИТЬ бути прокинутий у parent FastAPI (main.py).
    """
    app = mcp.http_app(path="/streamable", stateless_http=True)
    log.info("mcp_server_built", transport="streamable_http", path="/mcp/streamable")
    return app
