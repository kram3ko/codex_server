"""Side-effect imports — кожен модуль декорує тулу на `mcp_app`. Має
відпрацювати ДО `mount_mcp_server()`, інакше fastapi-mcp не дискаверить
ендпоінти. Додати тулу = додати файл і рядок сюди. `as X` — explicit
re-export per PEP 484 (linter знає що це навмисно)."""

from app.mcp.tools import errors as errors
from app.mcp.tools import notes as notes
from app.mcp.tools import show_image as show_image
