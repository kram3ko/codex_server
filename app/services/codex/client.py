"""Високорівневий клієнт до Codex CLI app-server.

Обгортає JSON-RPC handshake → thread/start → turn/start → стрім notifications,
перекладає Codex-сповіщення у наші типізовані ChatEvent'и. Один CodexClient =
одна сесія з sidecar'ом.

Thread state живе in-memory на стороні sidecar. Ми тримаємо `_thread_id` теж
in-memory, плюс caller може передати `initial_thread_id` (з БД cache) для
token-економії та `on_thread_change` callback для запису нового id.

Reconnect-семантика:
- Transport (WS) lost mid-stream → `_ensure_alive()` піднімає WS і робить
  re-handshake. Sidecar може бути той самий або новий — ми ще не знаємо.
- Якщо thread_id з БД stale (sidecar встиг рестартувати) → перший
  `turn/start` повертає `-32600 thread not found` → інвалідейтимо +
  відкриваємо новий thread + retry один раз.
"""

import asyncio
import mimetypes
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import orjson
import structlog
from pydantic import BaseModel, ConfigDict, Field

from app.services.codex.events import (
    Attachment,
    AttachmentKind,
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.codex.transport import AppServerClient, AppServerError, Notification

log = structlog.get_logger(__name__)

_CLIENT_INFO = {"name": "codex-api", "version": "0.1.0"}


class _Method(StrEnum):
    INITIALIZE = "initialize"
    INITIALIZED = "initialized"
    THREAD_START = "thread/start"
    THREAD_RESUME = "thread/resume"
    THREAD_INJECT_ITEMS = "thread/inject_items"
    TURN_START = "turn/start"
    TURN_STEER = "turn/steer"
    TURN_INTERRUPT = "turn/interrupt"
    ACCOUNT_RATE_LIMITS_READ = "account/rateLimits/read"


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

    Інші тули (без image-`path` у відповіді) → пуста tuple.
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
            log.warning("mcp_attachment_non_image_dropped", path=path, mime=mime)
            continue
        caption = data.get("caption")
        return _image_attachment(path, caption if isinstance(caption, str) else "")
    return ()


type _ItemExtractor[T] = Callable[[dict[str, Any]], T]
type _LabelGetter = str | _ItemExtractor[str]


# Optional UI-label override per MCP tool (operation_id → human label). Empty
# dict ⇒ label == operation_id raw — додавати рядки сюди коли захочеш
# локалізації або емодзі-префіксу для прогрес-бульбашки. Унікальний source of
# truth — `_mcp_label` нижче.
_MCP_TOOL_LABELS: dict[str, str] = {}


def _mcp_label(item: dict[str, Any]) -> str:
    """MCP item → label: friendly override якщо є, інакше operation_id."""
    tool = item.get("tool") or "mcp"
    return _MCP_TOOL_LABELS.get(tool, tool)


class _BuiltinSpec(BaseModel):
    """Декларативний опис builtin-Codex item'у: як його показати у TG progress
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
# Поля required у Rust (див. v2/item.rs) → юзаємо `item[...]` без fallback'а.
# Решта camelCase variants (collabAgent…, enteredReviewMode…) — у _HIDDEN_ITEMS.
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

type ThreadChangeCallback = Callable[[str | None], Awaitable[None]]


class CodexClient:
    """Один CodexClient = одна сесія з Codex CLI sidecar."""

    def __init__(
        self,
        url: str,
        cwd: str,
        approval_policy: str,
        sandbox: str,
        request_timeout: float = 60.0,
        initial_thread_id: str | None = None,
        on_thread_change: ThreadChangeCallback | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self._url = url
        self._cwd = cwd
        self._approval_policy = approval_policy
        self._sandbox = sandbox
        self._reasoning_effort = reasoning_effort
        self._transport = AppServerClient(url=url, request_timeout=request_timeout)
        self._initialized = False
        self._thread_id: str | None = initial_thread_id
        self._thread_resumed_or_started = False
        self._current_turn_id: str | None = None
        self._on_thread_change = on_thread_change
        self._alive_lock = asyncio.Lock()

    @property
    def current_thread_id(self) -> str | None:
        return self._thread_id

    async def connect(self) -> None:
        await self._transport.connect()
        await self._handshake()

    async def ensure_thread(self) -> str:
        """Ensure a usable thread_id is loaded into the sidecar.

        Three paths:
        1. We already opened a thread in this connection — reuse `_thread_id`.
        2. Caller passed a stored `initial_thread_id` — try `thread/resume`
           (sidecar reads it from disk). On success → reuse. On failure →
           treat as gone and open a fresh one.
        3. No id → `thread/start` opens a new thread.
        """
        if self._thread_id is not None and self._thread_resumed_or_started:
            return self._thread_id

        if self._thread_id is not None:
            stale = self._thread_id
            if await self._try_resume(stale):
                self._thread_resumed_or_started = True
                return stale
            log.info("codex_thread_resume_failed_opening_new", stale_thread_id=stale)
            self._thread_id = None
            await self._emit_thread_change(None)

        return await self._open_new_thread()

    async def _try_resume(self, thread_id: str) -> bool:
        try:
            await self._transport.request(_Method.THREAD_RESUME, {"threadId": thread_id})
        except AppServerError as exc:
            if _is_thread_not_found(exc) or exc.code == -32601:
                return False
            raise
        log.info("codex_thread_resumed", thread_id=thread_id)
        return True

    async def _open_new_thread(self) -> str:
        result = await self._transport.request(
            _Method.THREAD_START,
            {
                "cwd": self._cwd,
                "approvalPolicy": self._approval_policy,
                "sandbox": self._sandbox,
            },
        )
        thread_id: str = result["thread"]["id"]
        self._thread_id = thread_id
        self._thread_resumed_or_started = True
        log.info("codex_thread_opened", thread_id=thread_id)
        await self._emit_thread_change(thread_id)
        return thread_id

    async def run_turn(
        self,
        text: str,
        attachments: tuple[str, ...] = (),
    ) -> AsyncIterator[ChatEvent]:
        await self._ensure_alive()
        input_payload = self._build_input(text, attachments)
        result = await self._begin_turn_with_retry(input_payload)
        if isinstance(result, ErrorEvent):
            yield result
            return

        self._current_turn_id = _extract_turn_id(result)
        accumulated = ""

        async for note in self._transport.notifications():
            event = _translate(note, accumulated)
            if event is None:
                continue
            if isinstance(event, TokenEvent):
                accumulated += event.delta
            yield event
            if isinstance(event, DoneEvent):
                self._current_turn_id = None
                return

    async def interrupt(self) -> None:
        turn_id = self._current_turn_id
        if not turn_id:
            return
        try:
            await self._transport.request(_Method.TURN_INTERRUPT, {"turnId": turn_id})
        except AppServerError as exc:
            if exc.code == -32601:
                log.info("codex_interrupt_unsupported", turn_id=turn_id)
            else:
                log.warning("codex_interrupt_failed", turn_id=turn_id, code=exc.code)

    async def steer(self, text: str) -> bool:
        """Append text to in-flight turn. Returns True if accepted."""
        thread_id = self._thread_id
        turn_id = self._current_turn_id
        if not thread_id or not turn_id:
            return False
        try:
            await self._transport.request(
                _Method.TURN_STEER,
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": text}],
                    "expectedTurnId": turn_id,
                },
            )
        except AppServerError as exc:
            log.warning("codex_steer_failed", turn_id=turn_id, code=exc.code, msg=str(exc))
            return False
        log.info("codex_steered", turn_id=turn_id, text_len=len(text))
        return True

    async def inject_history(self, items: list[dict[str, Any]]) -> None:
        """Append Responses-API items into the current thread's history.

        Used after opening a fresh thread to seed it with prior turns from
        our DB — gives Codex context without thread/resume (which is broken
        upstream, see openai/codex#21360).
        """
        thread_id = self._thread_id
        if not thread_id or not items:
            return
        try:
            await self._transport.request(
                _Method.THREAD_INJECT_ITEMS,
                {"threadId": thread_id, "items": items},
            )
        except AppServerError as exc:
            log.warning("codex_inject_history_failed", code=exc.code, msg=str(exc))
            return
        log.info("codex_history_injected", thread_id=thread_id, items=len(items))

    async def read_rate_limits(self) -> dict[str, Any] | None:
        """Returns Codex plan rate-limit snapshot (account-level, no thread).
        None коли sidecar не expose'ить метод (-32601)."""
        try:
            return await self._transport.request(_Method.ACCOUNT_RATE_LIMITS_READ)
        except AppServerError as exc:
            if exc.code == -32601:
                return None
            raise

    async def start_new_thread(self) -> None:
        prev = self._thread_id
        self._thread_id = None
        self._thread_resumed_or_started = False
        self._current_turn_id = None
        if prev is not None:
            log.info("codex_thread_reset", prev_thread_id=prev)
            await self._emit_thread_change(None)

    async def close(self) -> None:
        await self._transport.close()

    async def _begin_turn_with_retry(
        self,
        input_payload: list[dict[str, Any]],
    ) -> dict[str, Any] | ErrorEvent:
        """One optimistic turn/start; on stale-thread → drop cache + retry once."""
        thread_id = await self.ensure_thread()
        try:
            return await self._transport.request(
                _Method.TURN_START,
                self._build_turn_params(thread_id, input_payload),
            )
        except AppServerError as exc:
            if not _is_thread_not_found(exc):
                return ErrorEvent(code="codex_error", detail=str(exc))
        log.info("codex_thread_stale_retrying", stale_thread_id=thread_id)
        await self._invalidate_thread()
        thread_id = await self.ensure_thread()
        try:
            return await self._transport.request(
                _Method.TURN_START,
                self._build_turn_params(thread_id, input_payload),
            )
        except AppServerError as exc:
            return ErrorEvent(code="codex_error", detail=str(exc))

    async def _invalidate_thread(self) -> None:
        self._thread_id = None
        self._thread_resumed_or_started = False
        self._current_turn_id = None
        await self._emit_thread_change(None)

    def _build_turn_params(
        self,
        thread_id: str,
        input_payload: list[dict[str, Any]],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"threadId": thread_id, "input": input_payload}
        if self._reasoning_effort:
            params["effort"] = self._reasoning_effort
        return params

    @staticmethod
    def _build_input(text: str, attachments: tuple[str, ...]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for attachment in attachments:
            parsed = urlparse(attachment)
            if parsed.scheme in {"http", "https"}:
                payload.append({"type": "image", "url": attachment})
            else:
                payload.append({"type": "localImage", "path": attachment})
        return payload

    async def _handshake(self) -> None:
        result = await self._transport.request(
            _Method.INITIALIZE,
            {"clientInfo": _CLIENT_INFO, "capabilities": {}},
        )
        await self._transport.notify(_Method.INITIALIZED, {})
        self._initialized = True
        log.info(
            "codex_handshake_done",
            user_agent=(result or {}).get("userAgent"),
            codex_home=(result or {}).get("codexHome"),
        )

    async def _ensure_alive(self) -> None:
        """Reconnect + re-handshake if transport died mid-stream."""
        async with self._alive_lock:
            if self._transport.is_connected and self._initialized:
                return
            log.warning(
                "codex_transport_lost",
                thread_id=self._thread_id,
                in_flight_turn=self._current_turn_id,
            )
            # Disconnect мід-turn → sidecar JSONL міг лишити orphan tool_call
            # (codex#14824). Resume такого thread'а вішає наступний turn.
            if self._current_turn_id is not None:
                await self._invalidate_thread()
            self._initialized = False
            await self._transport.connect()
            await self._handshake()

    async def _emit_thread_change(self, new_thread_id: str | None) -> None:
        if self._on_thread_change is None:
            return
        try:
            await self._on_thread_change(new_thread_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "codex_on_thread_change_failed",
                new=new_thread_id,
                error=str(exc),
            )


def _dynamic_tool_call_to_event(item: dict[str, Any]) -> ToolResultEvent:
    """Codex CLI dynamicToolCall (image_generation тощо) → typed event.

    `contentItems[].inputImage.imageUrl` стає `Attachment(kind=IMAGE, …)`,
    `inputText` зливається у `text`. Каллер шле binary напряму, без markdown
    round-trip'у через regex.
    """
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


def _is_thread_not_found(exc: AppServerError) -> bool:
    """Sidecar restarted → stored thread_id stale, retry with fresh thread."""
    return exc.code == -32600 and "thread not found" in str(exc).lower()


def _extract_turn_id(result: dict[str, Any]) -> str:
    return result["turn"]["id"]


def _translate(note: Notification, accumulated: str) -> ChatEvent | None:
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


def _generic_tool_call(item: dict[str, Any]) -> ToolCallEvent | None:
    tool_name = item.get("toolName")
    if tool_name is None:
        log.warning("codex_unknown_item_started", item_type=item["type"], keys=list(item.keys()))
        return None
    return ToolCallEvent(name=tool_name, args=item.get("arguments", {}))


def _generic_tool_result(item: dict[str, Any]) -> ToolResultEvent | None:
    tool_name = item.get("toolName")
    if tool_name is None:
        log.warning("codex_unknown_item_completed", item_type=item["type"], keys=list(item.keys()))
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
