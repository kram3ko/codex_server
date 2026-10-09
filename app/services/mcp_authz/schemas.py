from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.services.codex.sidecar import SidecarName


class McpAuthzRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class McpAuthzError(Exception):
    """Token signature/payload invalid."""


class McpAuthzClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: int = Field(description="DB users.id власника thread.")
    chat_id: int = Field(description="DB chats.id, до якого прив'язаний thread.")
    role: McpAuthzRole = Field(description="`admin` → full access; `user` → scoped по user_id.")
    sidecar: SidecarName = Field(description="Який Codex sidecar обслуговує thread.")
    iat: int = Field(description="Issued-at unix-timestamp.")
