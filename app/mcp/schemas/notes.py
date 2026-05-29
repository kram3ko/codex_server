"""note_save / note_search / note_list return shapes."""

from pydantic import BaseModel, ConfigDict, Field


class NoteRef(BaseModel):
    """Saved-note ack для `note_save`."""

    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    tags: list[str]
    updated_at: str = Field(description="ISO-8601 timestamp.")


class NoteHit(BaseModel):
    """Один елемент `note_search` / `note_list` results."""

    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    body: str
    tags: list[str]
    updated_at: str = Field(description="ISO-8601 timestamp.")
    rank: float | None = Field(
        default=None,
        description="FTS rank score; set лише `note_search`, None у `note_list`.",
    )
