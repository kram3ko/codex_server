from app.config import settings
from app.services.mcp_authz.service import McpAuthzService
from app.services.users.default import user_service

mcp_authz_service = McpAuthzService(settings.MCP_AUTHZ_SECRET, user_service)
