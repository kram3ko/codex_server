"""Codex sidecar notification → typed ChatEvent.

Translator stateless щодо турнів — `accumulated` приходить ззовні (від caller'а
що тримає state одного турну). TurnRouter гарантує що сюди потрапляють лише
ноти поточного турну, тож reconcile delta-stream ↔ item/completed чесний.
"""

import mimetypes
from collections.abc import Callable
from enum import StrEnum
from typing import Any

import orjson
from pydantic import BaseModel, ConfigDict, Field

from app.services.codex.events.types import (
    Attachment,
    AttachmentKind,
    ChatEvent,
    DoneEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.codex.transport import Notification


class CodexNotif(StrEnum):
    TURN_STARTED = "turn/started"
    TURN_COMPLETED = "turn/completed"
    AGENT_MSG_DELTA = "item/agentMessage/delta"
    ITEM_STARTED = "item/started"
    ITEM_COMPLETED = "item/completed"


class CodexItem(StrEnum):
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


_HIDDEN_ITEMS: frozenset[str] = frozenset(
    {
        CodexItem.USER_MESSAGE,
        CodexItem.HOOK_PROMPT,
        CodexItem.PLAN,
        CodexItem.REASONING,
        CodexItem.COLLAB_AGENT_TOOL_CALL,
        CodexItem.ENTERED_REVIEW_MODE,
        CodexItem.EXITED_REVIEW_MODE,
        CodexItem.CONTEXT_COMPACTION,
    }
)


def _command_args(item: dict[str, Any]) -> dict[str, Any]:
    cmd = item["command"]
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    return {"command": cmd}


def _image_attachment(source: str, caption: str = "") -> tuple[Attachment, ...]:
    return (Attachment(kind=AttachmentKind.IMAGE, source=source, caption=caption),)


def _mcp_attachments(item: dict[str, Any]) -> tuple[Attachment, ...]:
    """Витягти `Attachment` з MCP-результату (structuredContent або content[].text)."""
    result = item.get("result")
    if not isinstance(result, dict):
        return ()

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        attachment = _attachment_from_dict(structured)
        if attachment is not None:
            return (attachment,)

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
        attachment = _attachment_from_dict(data)
        if attachment is not None:
            return (attachment,)
    return ()


def _attachment_from_dict(data: dict[str, Any]) -> Attachment | None:
    path = data.get("path")
    if not isinstance(path, str) or not path:
        return None
    mime, _ = mimetypes.guess_type(path)
    if mime is None or not mime.startswith("image/"):
        return None
    caption = data.get("caption")
    return Attachment(
        kind=AttachmentKind.IMAGE,
        source=path,
        caption=caption if isinstance(caption, str) else "",
    )


type CodexItemExtractor[T] = Callable[[dict[str, Any]], T]
type _LabelGetter = str | CodexItemExtractor[str]


_MCP_TOOL_LABELS: dict[str, str] = {}


def _mcp_label(item: dict[str, Any]) -> str:
    tool = item.get("tool") or "mcp"
    return _MCP_TOOL_LABELS.get(tool, tool)


class _BuiltinSpec(BaseModel):
    """Декларативний опис builtin-Codex item'у: progress + ToolResultEvent."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    label: _LabelGetter = Field(description="UI-label: str або callable(item)→str.")
    args: CodexItemExtractor[dict[str, Any]] = Field(
        default=lambda _: {},
        description="Extract args для ToolCallEvent.args.",
    )
    text: CodexItemExtractor[str] = Field(
        default=lambda _: "",
        description="Extract text-вивід тулзи для ToolResultEvent.text.",
    )
    attachments: CodexItemExtractor[tuple[Attachment, ...]] = Field(
        default=lambda _: (),
        description="Extract media-артефакти для ToolResultEvent.attachments.",
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


_BUILTINS: dict[str, _BuiltinSpec] = {
    CodexItem.COMMAND_EXECUTION: _BuiltinSpec(
        label="shell",
        args=_command_args,
        text=lambda item: item.get("aggregatedOutput") or "",
    ),
    CodexItem.FILE_CHANGE: _BuiltinSpec(
        label="file_change",
        args=lambda item: {"changes": item["changes"]},
    ),
    CodexItem.WEB_SEARCH: _BuiltinSpec(
        label="web_search",
        args=lambda item: {"query": item["query"]},
    ),
    CodexItem.MCP_TOOL_CALL: _BuiltinSpec(
        label=_mcp_label,
        args=lambda item: {"server": item["server"], "tool": item["tool"]},
        attachments=_mcp_attachments,
    ),
    CodexItem.IMAGE_GENERATION: _BuiltinSpec(
        # `revisedPrompt` від OpenAI Image API завжди англ — НЕ як TG caption.
        label="image_generation",
        args=lambda item: {"prompt": item.get("revisedPrompt", "")},
        text=lambda item: item.get("revisedPrompt", ""),
        attachments=lambda item: (
            _image_attachment(item["savedPath"], "") if item.get("savedPath") else ()
        ),
    ),
    CodexItem.IMAGE_VIEW: _BuiltinSpec(
        label="image_view",
        args=lambda item: {"path": item["path"]},
        attachments=lambda item: _image_attachment(item["path"]),
    ),
}


def translate_notification(note: Notification, accumulated: str) -> ChatEvent | None:
    """Codex notification → typed ChatEvent.

    `accumulated` — text streamed via agentMessage/delta so far (turn-scoped).
    Caller тримає його і ресетить між турнами. TurnRouter гарантує що сюди
    не потрапляють ноти чужих турнів.
    """
    match note.method:
        case CodexNotif.TURN_STARTED:
            return None
        case CodexNotif.AGENT_MSG_DELTA:
            delta: str = note.params["delta"]
            return TokenEvent(delta=delta) if delta else None
        case CodexNotif.ITEM_STARTED:
            return _on_item_started(note.params["item"])
        case CodexNotif.ITEM_COMPLETED:
            return _on_item_completed(note.params["item"], accumulated)
        case CodexNotif.TURN_COMPLETED:
            return DoneEvent(final_text=note.params.get("finalText", accumulated))
        case _:
            return None


def _on_item_started(item: dict[str, Any]) -> ChatEvent | None:
    item_type: str = item["type"]
    if item_type == CodexItem.AGENT_MESSAGE or item_type in _HIDDEN_ITEMS:
        return None
    spec = _BUILTINS.get(item_type)
    if spec is not None:
        return spec.to_started(item)
    return _generic_tool_call(item)


def _on_item_completed(item: dict[str, Any], accumulated: str) -> ChatEvent | None:
    item_type: str = item["type"]
    if item_type == CodexItem.AGENT_MESSAGE:
        return _agent_message_to_token(item, accumulated)
    if item_type in _HIDDEN_ITEMS:
        return None
    if item_type == CodexItem.DYNAMIC_TOOL_CALL:
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
    """Reconcile fallback item/completed agentMessage with delta-stream.

    Three cases:
    - text fully streamed already (accumulated ends with text)        → None
    - text extends accumulated (startswith)                            → suffix delta
    - divergence (model rewrote earlier tokens, whitespace mismatch)   → None.
      DoneEvent.final_text несе авторитетну версію; персистимо її у БД,
      web/TG на reload показують правильний текст. Краще не дублювати
      бабблу всередині турну — це і був той самий «бомба»-кейс.
    """
    text: str = item["text"]
    if not text or accumulated.endswith(text):
        return None
    if text.startswith(accumulated):
        return TokenEvent(delta=text[len(accumulated) :])
    return None
