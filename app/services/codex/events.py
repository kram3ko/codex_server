"""Типізовані chat-event'и між Codex client'ом і WebSocket-handler'ом.

Pydantic v2 моделі — кожне поле з `Field(description=...)`, тому JSON Schema
генерується із самих типів, а wire-frame має чітку документацію.

Серіалізація — orjson, bytes-native, без проміжних `str` чи ascii-fallback'ів.
Збираємо з `ORJSON_BUILD_FREETHREADED=1` у Dockerfile (3.14t opt-in).
"""

import asyncio
import mimetypes
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import StrEnum
from typing import Any, ClassVar, TypedDict, cast

import orjson
from pydantic import BaseModel, ConfigDict, Field

from app.services.codex.transport import Notification


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
    cls.type_tag: cls for cls in (TokenEvent, ToolCallEvent, ToolResultEvent, DoneEvent, ErrorEvent)
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
    return cast(ChatEvent, cls.model_validate(payload))


def bytes_to_event(raw: bytes | str) -> ChatEvent:
    return frame_to_event(orjson.loads(raw))


class _Notif(StrEnum):
    TURN_STARTED = "turn/started"
    TURN_COMPLETED = "turn/completed"
    AGENT_MSG_DELTA = "item/agentMessage/delta"
    ITEM_STARTED = "item/started"
    ITEM_COMPLETED = "item/completed"


class _Item(StrEnum):
    # Source of truth: openai/codex codex-rs/app-server-protocol/src/protocol/v2/item.rs
    AGENT_MESSAGE = "agentMessage"
    USER_MESSAGE = "userMessage"
    HOOK_PROMPT = "hookPrompt"
    PLAN = "plan"
    REASONING = "reasoning"
    COMMAND_EXECUTION = "commandExecution"
    FILE_CHANGE = "fileChange"
    MCP_TOOL_CALL = "mcpToolCall"
    DYNAMIC_TOOL_CALL = "dynamicToolCall"
    COLLAB_AGENT_TOOL_CALL = "collabAgentToolCall"
    WEB_SEARCH = "webSearch"
    IMAGE_VIEW = "imageView"
    IMAGE_GENERATION = "imageGeneration"
    ENTERED_REVIEW_MODE = "enteredReviewMode"
    EXITED_REVIEW_MODE = "exitedReviewMode"
    CONTEXT_COMPACTION = "contextCompaction"


# Items that don't surface as progress events. agentMessage is hidden in the
# `started` branch but extracted as a TokenEvent in `completed` (the long
# fallback path when sidecar emits whole text instead of streaming deltas).
_HIDDEN_ITEMS: frozenset[str] = frozenset(
    {
        _Item.USER_MESSAGE,
        _Item.HOOK_PROMPT,
        _Item.PLAN,
        _Item.REASONING,
        _Item.COLLAB_AGENT_TOOL_CALL,
        _Item.ENTERED_REVIEW_MODE,
        _Item.EXITED_REVIEW_MODE,
        _Item.CONTEXT_COMPACTION,
    }
)


def _command_args(item: dict[str, Any]) -> dict[str, Any]:
    # `command` був list[str] у v1, став str у v2 — shape-bridge між версіями.
    cmd = item["command"]
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    return {"command": cmd}


def _image_attachment(source: str, caption: str = "") -> tuple[Attachment, ...]:
    return (Attachment(kind=AttachmentKind.IMAGE, source=source, caption=caption),)


def _mcp_attachments(item: dict[str, Any]) -> tuple[Attachment, ...]:
    """Витягти `Attachment` з MCP-результату.

    Контракт: MCP-тула повертає `{"path": "<image>", "caption": "..."}`.
    Path має бути image MIME (mimetypes.guess_type) — інакше ризик надіслати
    .env / .json як photo. Trusted-root перевіряється далі у
    `upload_service.persist_attachments`.
    """
    result = item.get("result")
    if not isinstance(result, dict):
        return ()
    for block in result.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text") or ""
        try:
            data = orjson.loads(text)
        except orjson.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        path = data.get("path")
        if not isinstance(path, str) or not path:
            continue
        mime, _ = mimetypes.guess_type(path)
        if mime is None or not mime.startswith("image/"):
            continue
        caption = data.get("caption")
        return _image_attachment(path, caption if isinstance(caption, str) else "")
    return ()


type _ItemExtractor[T] = Callable[[dict[str, Any]], T]
type _LabelGetter = str | _ItemExtractor[str]


# Optional UI-label override per MCP tool (operation_id → human label). Empty
# dict ⇒ label == operation_id raw — додавати рядки сюди коли захочеш
# локалізації або емодзі-префіксу для прогрес-бульбашки.
_MCP_TOOL_LABELS: dict[str, str] = {}


def _mcp_label(item: dict[str, Any]) -> str:
    """MCP item → label: friendly override якщо є, інакше operation_id."""
    tool = item.get("tool") or "mcp"
    return _MCP_TOOL_LABELS.get(tool, tool)


class _BuiltinSpec(BaseModel):
    """Декларативний опис builtin-Codex item'у: як його показати у progress
    і як зібрати ToolResultEvent (text + attachments)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    label: _LabelGetter = Field(
        description=(
            "UI-label. Статичний str для билт-інів (`shell`, `image_generation`),"
            " або callable(item)→str для динамічних (MCP — лейбл з operation_id)."
        ),
    )
    args: _ItemExtractor[dict[str, Any]] = Field(
        default=lambda _: {},
        description="Extract args dict з raw item payload для ToolCallEvent.args.",
    )
    text: _ItemExtractor[str] = Field(
        default=lambda _: "",
        description="Extract text-вивід тулзи для ToolResultEvent.text.",
    )
    attachments: _ItemExtractor[tuple[Attachment, ...]] = Field(
        default=lambda _: (),
        description="Extract структурні media-артефакти для ToolResultEvent.attachments.",
    )

    def _resolve_label(self, item: dict[str, Any]) -> str:
        return self.label(item) if callable(self.label) else self.label

    def to_started(self, item: dict[str, Any]) -> ToolCallEvent:
        return ToolCallEvent(name=self._resolve_label(item), args=self.args(item))

    def to_completed(self, item: dict[str, Any]) -> ToolResultEvent:
        return ToolResultEvent(
            name=self._resolve_label(item),
            text=self.text(item),
            attachments=self.attachments(item),
            error=item.get("error"),
        )


# Codex builtin item-types які surface'имо як pseudo-tools у progress'і.
_BUILTINS: dict[str, _BuiltinSpec] = {
    _Item.COMMAND_EXECUTION: _BuiltinSpec(
        label="shell",
        args=_command_args,
        text=lambda item: item.get("aggregatedOutput") or "",
    ),
    _Item.FILE_CHANGE: _BuiltinSpec(
        label="file_change",
        args=lambda item: {"changes": item["changes"]},
    ),
    _Item.WEB_SEARCH: _BuiltinSpec(
        label="web_search",
        args=lambda item: {"query": item["query"]},
    ),
    _Item.MCP_TOOL_CALL: _BuiltinSpec(
        label=_mcp_label,
        args=lambda item: {"server": item["server"], "tool": item["tool"]},
        attachments=_mcp_attachments,
    ),
    _Item.IMAGE_GENERATION: _BuiltinSpec(
        label="image_generation",
        # `revisedPrompt` від OpenAI Image API завжди англ — тримаємо у args
        # для меми/трейсу, але НЕ робимо з нього TG caption: модель пише власну
        # відповідь у мові юзера окремою бульбашкою перед фото.
        args=lambda item: {"prompt": item.get("revisedPrompt", "")},
        text=lambda item: item.get("revisedPrompt", ""),
        attachments=lambda item: (
            _image_attachment(item["savedPath"], "") if item.get("savedPath") else ()
        ),
    ),
    _Item.IMAGE_VIEW: _BuiltinSpec(
        label="image_view",
        args=lambda item: {"path": item["path"]},
        attachments=lambda item: _image_attachment(item["path"]),
    ),
}


def translate_notification(note: Notification, accumulated: str) -> ChatEvent | None:
    match note.method:
        case _Notif.TURN_STARTED:
            return None
        case _Notif.AGENT_MSG_DELTA:
            delta: str = note.params["delta"]
            return TokenEvent(delta=delta) if delta else None
        case _Notif.ITEM_STARTED:
            return _on_item_started(note.params["item"])
        case _Notif.ITEM_COMPLETED:
            return _on_item_completed(note.params["item"], accumulated)
        case _Notif.TURN_COMPLETED:
            return DoneEvent(final_text=note.params.get("finalText", accumulated))
        case _:
            return None


def _on_item_started(item: dict[str, Any]) -> ChatEvent | None:
    item_type: str = item["type"]
    if item_type == _Item.AGENT_MESSAGE or item_type in _HIDDEN_ITEMS:
        return None
    spec = _BUILTINS.get(item_type)
    if spec is not None:
        return spec.to_started(item)
    return _generic_tool_call(item)


def _on_item_completed(item: dict[str, Any], accumulated: str) -> ChatEvent | None:
    item_type: str = item["type"]
    if item_type == _Item.AGENT_MESSAGE:
        return _agent_message_to_token(item, accumulated)
    if item_type in _HIDDEN_ITEMS:
        return None
    if item_type == _Item.DYNAMIC_TOOL_CALL:
        return _dynamic_tool_call_to_event(item)
    spec = _BUILTINS.get(item_type)
    if spec is not None:
        return spec.to_completed(item)
    return _generic_tool_result(item)


def _dynamic_tool_call_to_event(item: dict[str, Any]) -> ToolResultEvent:
    """Codex CLI dynamicToolCall (image_generation тощо) → typed event."""
    text_parts: list[str] = []
    attachments: list[Attachment] = []
    tool_name: str = item["tool"]
    for content in item["contentItems"]:
        match content["type"]:
            case "inputText":
                text_parts.append(content["text"])
            case "inputImage":
                attachments.append(
                    Attachment(
                        kind=AttachmentKind.IMAGE,
                        source=content["imageUrl"],
                        caption=tool_name,
                    )
                )
    return ToolResultEvent(
        name=tool_name,
        text="\n\n".join(p for p in text_parts if p.strip()),
        attachments=tuple(attachments),
        error=item.get("error"),
    )


def _generic_tool_call(item: dict[str, Any]) -> ToolCallEvent | None:
    tool_name = item.get("toolName")
    if tool_name is None:
        return None
    return ToolCallEvent(name=tool_name, args=item.get("arguments", {}))


def _generic_tool_result(item: dict[str, Any]) -> ToolResultEvent | None:
    tool_name = item.get("toolName")
    if tool_name is None:
        return None
    return ToolResultEvent(
        name=tool_name,
        text=item.get("output", ""),
        error=item.get("error"),
    )


def _agent_message_to_token(item: dict[str, Any], accumulated: str) -> ChatEvent | None:
    # Sidecar may emit the whole agent-message as one item instead of streaming
    # `agentMessage/delta`. Reconcile against `accumulated` to avoid double text.
    text: str = item["text"]
    if not text or accumulated.endswith(text):
        return None
    if text.startswith(accumulated):
        return TokenEvent(delta=text[len(accumulated) :])
    return TokenEvent(delta=text)


async def iterate_with_idle_timeout(
    stream: AsyncIterator[ChatEvent],
    idle_s: float,
    *,
    on_idle: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[ChatEvent]:
    """Stream-watchdog: ресетить таймер на кожен yield. Спрацьовує idle_s
    тиші між events → on_idle (якщо є) + TimeoutError."""
    while True:
        try:
            async with asyncio.timeout(idle_s):
                event = await anext(stream)
        except StopAsyncIteration:
            return
        except TimeoutError:
            if on_idle is not None:
                await on_idle()
            raise
        yield event
