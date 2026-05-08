"""show_image: re-deliver a previously generated image без regen.

Валідуємо що `path` під trusted root, повертаємо canonical path.
TG/MinIO робить pipeline-side `_persist_assistant_turn`, не ця тула.
"""

from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from pydantic import Field

from app.mcp.core import mcp
from app.utils.paths import resolve_trusted_local_path


@mcp.tool(
    description=(
        "Re-deliver an image you already generated — without calling "
        "image_generation again. Use whenever you'd regenerate the same "
        "picture: saves tokens, gives the exact image. Never paste markdown "
        "![](...) or raw paths in reply text."
    ),
)
async def show_image(
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
) -> dict[str, Any]:
    local_path = resolve_trusted_local_path(path)
    if local_path is None:
        raise ToolError(f"file_not_found_or_untrusted: {path}")
    return {
        "path": str(local_path),
        "filename": local_path.name,
        "caption": caption,
        "delivered": True,
    }
