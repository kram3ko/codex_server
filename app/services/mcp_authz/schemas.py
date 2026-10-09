from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.services.codex.sidecar import SidecarName


class McpAuthzRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class McpAuthzError(Exception):
    """Token invalid or its subject no longer exists."""


class McpAuthzClaims(BaseModel):
    """Payload of the per-thread token: who owns the thread, not what they may do."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: int = Field(description="DB users.id власника thread.")
    chat_id: int = Field(description="DB chats.id, до якого прив'язаний thread.")
    sidecar: SidecarName = Field(description="Який Codex sidecar обслуговує thread.")
    iat: int = Field(description="Issued-at unix-timestamp.")


class McpIdentity(BaseModel):
    """Caller identity resolved against the DB at tool-call time."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    chat_id: int
    role: McpAuthzRole = Field(description="`admin` → full access; `user` → scoped по user_id.")
