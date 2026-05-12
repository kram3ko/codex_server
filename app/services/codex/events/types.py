"""Типізовані ChatEvent'и між Codex client'ом і WebSocket-handler'ом.

Pydantic v2 — JSON Schema генерується із самих типів, wire-frame документований.
Серіалізація — orjson, bytes-native.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar, TypedDict, cast

import orjson
from pydantic import BaseModel, ConfigDict, Field


class ToolCallRecord(TypedDict):
    """Згорнутий tool-call для `messages.meta.calls` (mirror `ToolCallEvent.{name, args}`)."""

    name: str
    args: dict[str, Any]


class AttachmentKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    FILE = "file"


class _Frame(BaseModel):
    """Базовий frozen-frame: усі ChatEvent наслідують і несуть дискриминатор."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type_tag: ClassVar[str]


class Attachment(_Frame):
    """Один файл/медіа-артефакт, що його тулза прислала разом з результатом."""

    type_tag: ClassVar[str] = "attachment"
    kind: AttachmentKind = Field(description="Тип контенту: image / audio / file.")
    source: str = Field(description="URL або локальний шлях під довіреним коренем.")
    caption: str = Field(default="", description="Caption або alt-текст (порожній — без підпису).")


class TokenEvent(_Frame):
    type_tag: ClassVar[str] = "token"
    delta: str = Field(description="Інкрементальний шматок agentMessage стріму.")


class ToolCallEvent(_Frame):
    type_tag: ClassVar[str] = "tool_call"
    name: str = Field(description="Ім'я тулзи (Codex builtin label чи toolName).")
    args: dict[str, Any] = Field(description="Аргументи виклику як отримано від Codex.")


class ToolResultEvent(_Frame):
    type_tag: ClassVar[str] = "tool_result"
    name: str = Field(description="Ім'я тулзи, результат якої прийшов.")
    text: str = Field(default="", description="Текстовий вивід тулзи (stdout, опис тощо).")
    attachments: tuple[Attachment, ...] = Field(
        default=(), description="Файли/медіа, що їх тулза вкладає у результат."
    )
    error: str | None = Field(default=None, description="Текст помилки, якщо тулза впала.")


class DoneEvent(_Frame):
    type_tag: ClassVar[str] = "done"
    final_text: str = Field(description="Фінальний agentMessage після завершення турну.")


class ErrorEvent(_Frame):
    type_tag: ClassVar[str] = "error"
    code: str = Field(description="Стабільний machine-readable код (наприклад, 'codex_error').")
    detail: str | None = Field(default=None, description="Людинозрозумілий опис помилки.")


ChatEvent = TokenEvent | ToolCallEvent | ToolResultEvent | DoneEvent | ErrorEvent


_TAG_TO_CLS: dict[str, type[_Frame]] = {
    cls.type_tag: cls for cls in (TokenEvent, ToolCallEvent, ToolResultEvent, DoneEvent, ErrorEvent)
}


def event_to_frame(event: ChatEvent) -> dict[str, Any]:
    """Дискриминатор `type` + плоский dump."""
    return {"type": event.type_tag, **event.model_dump(mode="json")}


def event_to_bytes(event: ChatEvent) -> bytes:
    """Hot-path серіалізатор для bus / WS."""
    return orjson.dumps(event_to_frame(event))


def frame_to_event(frame: Mapping[str, Any]) -> ChatEvent:
    cls = _TAG_TO_CLS[frame["type"]]
    payload = {k: v for k, v in frame.items() if k != "type"}
    return cast(ChatEvent, cls.model_validate(payload))


def bytes_to_event(raw: bytes | str) -> ChatEvent:
    return frame_to_event(orjson.loads(raw))