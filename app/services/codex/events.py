"""Типізовані chat-event'и між Codex client'ом і WebSocket-handler'ом.

Pydantic v2 моделі — кожне поле з `Field(description=...)`, тому JSON Schema
генерується із самих типів, а wire-frame має чітку документацію.

Серіалізація — orjson, bytes-native, без проміжних `str` чи ascii-fallback'ів.
Збираємо з `ORJSON_BUILD_FREETHREADED=1` у Dockerfile (3.14t opt-in).
"""

from enum import StrEnum
from typing import Any, ClassVar, TypedDict

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
    """Базовий frozen-frame: усі ChatEvent'и наслідують і несуть дискриминатор."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Перевизначається у кожного підкласу — використовується для wire-tag'а.
    type_tag: ClassVar[str]


class Attachment(_Frame):
    """Один файл/медіа-артефакт, що його тулза прислала разом з результатом.

    `source` — URL або локальний шлях під довіреним коренем. Рендерер сам
    ресолвить його (Telegram → bytes, web → presigned URL); markdown bridge
    більше не потрібен.
    """

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
    cls.type_tag: cls
    for cls in (TokenEvent, ToolCallEvent, ToolResultEvent, DoneEvent, ErrorEvent)
}


def event_to_frame(event: ChatEvent) -> dict[str, Any]:
    """Дискриминатор `type` + плоский dump. Серіалізація — окремо (orjson)."""
    return {"type": event.type_tag, **event.model_dump(mode="json")}


def event_to_bytes(event: ChatEvent) -> bytes:
    """Hot-path серіалізатор для bus / WS (orjson — bytes-native, no copies)."""
    return orjson.dumps(event_to_frame(event))


def frame_to_event(frame: dict[str, Any]) -> ChatEvent:
    cls = _TAG_TO_CLS[frame["type"]]
    payload = {k: v for k, v in frame.items() if k != "type"}
    return cls.model_validate(payload)  # type: ignore[return-value]


def bytes_to_event(raw: bytes | str) -> ChatEvent:
    return frame_to_event(orjson.loads(raw))
