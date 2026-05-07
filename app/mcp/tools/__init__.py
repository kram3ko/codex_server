"""Side-effect imports — кожен модуль декорує тулу на `mcp_app`. Має
відпрацювати ДО `mount_mcp_server()`, інакше fastapi-mcp не дискаверить
ендпоінти. Додати тулу = додати файл і рядок сюди."""

from app.mcp.tools import show_image  # noqa: F401
