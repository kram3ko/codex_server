from app.config import settings
from app.services.mcp_authz.service import McpAuthzService

mcp_authz_service = McpAuthzService(settings.MCP_AUTHZ_SECRET)
