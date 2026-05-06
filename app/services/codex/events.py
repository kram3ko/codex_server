"""Типізовані chat-event'и між Codex client'ом і WebSocket-handler'ом.

Дискриминатор — клас (тут) → 'type' string у wire-frame'і (через
`event_to_frame`). Усі поля плоскі, JSON-friendly.
"""

import dataclasses
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenEvent:
    delta: str


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResultEvent:
    name: str
    result: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DoneEvent:
    final_text: str


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    code: str
    detail: str | None = None


ChatEvent = TokenEvent | ToolCallEvent | ToolResultEvent | DoneEvent | ErrorEvent


_TYPE_TAG = {
    TokenEvent: "token",
    ToolCallEvent: "tool_call",
    ToolResultEvent: "tool_result",
    DoneEvent: "done",
    ErrorEvent: "error",
}


def event_to_frame(ev: ChatEvent) -> dict[str, Any]:
    """Серіалізація в wire-frame з discriminator-полем `type`."""
    tag = _TYPE_TAG.get(type(ev))
    if tag is None:
        raise TypeError(f"unknown ChatEvent subtype: {type(ev).__name__}")
    return {"type": tag, **dataclasses.asdict(ev)}
