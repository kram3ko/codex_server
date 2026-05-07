"""show_image: re-deliver a previously generated/uploaded image без regen.

Модель передає `path` файлу що вже лежить під trusted root (зазвичай
`/home/codex/.codex/generated_images/<thread>/...png`, бо image_generation
sidecar'а пише саме туди). Ми валідуємо що шлях під trusted root і повертаємо
canonical path — pipeline-side `_BUILTINS["mcp"]` витягне його з
`McpToolCallResult.content[0].text`, побудує `Attachment(IMAGE)` і існуючим
шляхом покаже у TG + збереже у MinIO як `Upload` row.

Тула не вантажить файл сама, бо MinIO upload + Telegram send уже централізовані
у `_persist_assistant_turn` → `upload_service.persist_attachments` →
`send_attachment`. Дублювати тут було б розщепленням відповідальності.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.mcp.core import mcp_app
from app.mcp.errors import ToolError
from app.utils.paths import resolve_trusted_local_path


class _ShowImageBody(BaseModel):
    path: str = Field(
        ...,
        description=(
            "savedPath from an earlier image_generation, "
            "under /home/codex/.codex/generated_images/."
        ),
    )
    caption: str | None = Field(
        None,
        max_length=500,
        description="Optional one-line caption (same language as user's request).",
    )


@mcp_app.post(
    "/tools/show_image",
    operation_id="show_image",
    summary=(
        "Re-deliver an image you already generated — without calling "
        "image_generation again. Use whenever you'd regenerate the same "
        "picture: saves tokens, gives the exact image. Never paste markdown "
        "![](...) or raw paths in reply text."
    ),
)
async def tool_show_image(body: _ShowImageBody) -> dict[str, Any]:
    local_path = resolve_trusted_local_path(body.path)
    if local_path is None:
        raise ToolError(
            f"file_not_found_or_untrusted: {body.path}",
            code="invalid_input",
        )
    return {
        "path": str(local_path),
        "filename": local_path.name,
        "caption": body.caption,
        "delivered": True,
    }
