"""show_image return shape — MCP-tool у `app/mcp/tools/show_image.py` віддає її."""

from pydantic import BaseModel, ConfigDict


class ImageDelivery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    filename: str
    caption: str | None = None
    delivered: bool = True
