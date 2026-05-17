"""DTO для turn lifecycle. `TurnStatus` re-export з models — там single source."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TurnStatus
from app.services.codex.sidecar import SidecarName

__all__ = ["TurnCreate", "TurnRow", "TurnStatus"]


class TurnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int = Field(description="FK до chats.id")
    user_id: int = Field(description="Власник turn-а (FK users.id)")
    user_message_id: int = Field(description="Persisted USER message що тригернув turn")
    sidecar: SidecarName = Field(description="Codex CLI sidecar (admin/guest)")


class TurnRow(BaseModel):
    """Read DTO. Маппиться з `app.models.Turn` ORM через `model_validate`."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int = Field(description="Postgres turn pk — primary handle усього lifecycle-у")
    chat_id: int = Field(description="FK chats.id")
    user_id: int = Field(description="Власник turn-а")
    user_message_id: int = Field(description="USER message що тригернув turn")
    assistant_message_id: int | None = Field(
        description="Lazy-bind після першого видимого token-а",
    )
    status: TurnStatus = Field(description="Lifecycle state machine")
    codex_thread_id: str | None = Field(description="Codex thread id; None до turn/start")
    codex_turn_id: str | None = Field(description="Codex turn id; None до turn/start")
    sidecar: SidecarName | None = Field(description="Codex CLI sidecar (admin/guest)")
    stream_key: str = Field(description="Redis stream key для live events (turn:{id}:events)")
    error_code: str | None = Field(description="Domain error code на terminal failed/cancelled")
    error_detail: str | None = Field(description="Free-form error context для debug")
    last_event_id: str | None = Field(description="Cursor для client-side reconnect replay")
    heartbeat_at: datetime = Field(description="Worker liveness signal; cleaner ловить orphan-и")
    completed_at: datetime | None = Field(description="Terminal moment; None поки active")
    created_at: datetime = Field(description="STARTING insert moment (Base auto)")
    updated_at: datetime = Field(description="Any mutation tick (Base auto)")
