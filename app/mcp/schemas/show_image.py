"""show_image return shape — MCP-tool у `app/mcp/tools/show_image.py` віддає її."""

from pydantic import BaseModel, ConfigDict, Field


class ImageDelivery(BaseModel):
    """Output контракт MCP-tool `show_image` → LLM. Наш controlled DTO."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Локальний шлях під довіреним коренем.")
    filename: str = Field(description="Базове ім'я файла (для UI/caption).")
    caption: str | None = Field(default=None, description="Caption від tool-caller'а.")
    delivered: bool = Field(default=True, description="Чи Telegram отримав фото.")
