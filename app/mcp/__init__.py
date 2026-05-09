"""FastMCP server + ASGI app. Side-effect import тул ДО build — інакше
FastMCP не побачить @mcp.tool на момент створення routes."""

from app.mcp.core import build_mcp_http_app, mcp
from app.mcp.tools import *  # noqa: F401,F403 — register @mcp.tool decorators

mcp_http_app = build_mcp_http_app()

__all__ = ["mcp", "mcp_http_app"]
