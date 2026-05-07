"""MCP sub-app — auth, tools, mount.

`main.py` робить:
    from app.mcp import mcp_app, mount_mcp_server
    mount_mcp_server()
    app.mount("/mcp", mcp_app)

Side-effect import тул мусить бути ДО `mount_mcp_server()`.
"""

from app.mcp.core import mcp_app, mount_mcp_server
from app.mcp.tools import *  # noqa: F401,F403 — register tool endpoints

__all__ = ["mcp_app", "mount_mcp_server"]
