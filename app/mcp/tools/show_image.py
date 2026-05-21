"""show_image: re-deliver a previously generated image без regen.

Валідуємо що `path` під trusted root, повертаємо canonical path.
TG/MinIO робить pipeline-side `_persist_assistant_turn`, не ця тула.

Return shape — `app/mcp/schemas/show_image.py` (Pydantic).
"""

from typing import Annotated

from fastmcp.exceptions import ToolError
from pydantic import Field

from app.mcp.core import mcp
from app.mcp.schemas.show_image import ImageDelivery
from app.services.mcp_authz import McpAuthzError, verify_authz
from app.utils.paths import resolve_trusted_local_path


@mcp.tool(name="show_image")
async def show_image(
    authz: Annotated[
        str,
        Field(
            description=(
                "JWT з prompt-header `MCPAuthz: <token>`. Verify-ить що виклик "
                "автентичний (з активного turn'у codex-server)."
            ),
        ),
    ],
    path: Annotated[
        str,
        Field(
            description=(
                "Exact savedPath from an earlier image_generation "
                "(under /home/codex/.codex/generated_images/, ends with the "
                "real <uuid>.png filename — do NOT pass placeholders)."
            ),
        ),
    ],
    caption: Annotated[
        str | None,
        Field(
            max_length=500,
            description="Optional one-line caption (same language as user's request).",
        ),
    ] = None,
) -> ImageDelivery:
    """Re-deliver an image you already generated — without calling
    image_generation again. Use whenever you'd regenerate the same picture:
    saves tokens, gives the exact image. Never paste markdown ![](...) or
    raw paths in reply text.
    """
    try:
        verify_authz(authz)
    except McpAuthzError as exc:
        raise ToolError(str(exc)) from exc
    local_path = resolve_trusted_local_path(path)
    if local_path is None:
        raise ToolError(f"file_not_found_or_untrusted: {path}")
    return ImageDelivery(
        path=str(local_path),
        filename=local_path.name,
        caption=caption,
    )
