"""Bugsink return shapes — MCP-tools у `app/mcp/tools/errors.py` віддають їх.

`IssueSummary`: поля = LLM-friendly short names; `validation_alias` мапить
сирий Bugsink JSON (`calculated_type` → `type`, …) на парсингу через
`model_validate(...)`. Output JSON-schema і dump'и йдуть з короткими іменами
(field names), не з alias'ами.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IssueSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: str | None = Field(default=None, validation_alias="calculated_type")
    message: str | None = Field(default=None, validation_alias="calculated_value")
    events: int | None = Field(default=None, validation_alias="stored_event_count")
    last_seen: str | None = None
    resolved: bool | None = Field(default=None, validation_alias="is_resolved")


class EventDetail(BaseModel):
    """Whitelist over Bugsink event. Сирий `data` несе request body, headers,
    breadcrumbs з user-prompt'ами — LLM це бачити не повинен; whitelist'имо
    тільки нешкідливий контекст (platform/level/release/etc.)."""

    model_config = ConfigDict(extra="ignore")

    issue_id: str
    event_id: str
    timestamp: str | None = None
    stacktrace: str = ""
    platform: str | None = None
    level: str | None = None
    logger: str | None = None
    environment: str | None = None
    release: str | None = None
    transaction: str | None = None
    tags: dict[str, Any] | list[Any] | None = None
