"""Codex ChatEvent ↔ protobuf converters + outbound text safety.

Окремо від `app/rpc/_mappers.py` (там ORM→pb): тут конверсії з internal
ChatEvent layer + redaction для error-стрінгів що йдуть наружу.
"""

import structlog

from app.grpc_generated.codex.v1 import chat_pb2
from app.rpc._mappers import to_struct, to_ts
from app.services.codex.events import (
    Attachment,
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.codex_usage.service import CodexUsage, UsageWindow

log = structlog.get_logger(__name__)

# Presigned S3/MinIO URLs leak access key + signature + path в error-стрінгах.
# Token-replace перед видачею клієнту; raw — у structlog для debugging.
_PRESIGNED_MARKER = "X-Amz-Signature="
_REDACTED_URL = "[link expired or unavailable]"

# Image generation file path → public web URL (рендериться під час stream'у
# без чекання S3 persist).
_GENERATED_PREFIX = "/home/codex/.codex/generated_images/"


def redact_for_user(text: str, *, source: str) -> str:
    """Strip presigned-URL noise. Raw — у structlog щоб не глушити debugging."""
    if _PRESIGNED_MARKER not in text:
        return text
    redacted = " ".join(_REDACTED_URL if _PRESIGNED_MARKER in tok else tok for tok in text.split())
    log.warning("user_facing_error_redacted", source=source, raw=text)
    return redacted


def attachment_to_pb(attachment: Attachment) -> chat_pb2.Attachment:
    source = attachment.source
    if source.startswith(_GENERATED_PREFIX):
        source = "/generated/" + source[len(_GENERATED_PREFIX) :]
    return chat_pb2.Attachment(
        kind=attachment.kind.value,
        source=source,
        caption=attachment.caption,
    )


def error_event(code: str, detail: str | None = None) -> chat_pb2.ChatEvent:
    error = chat_pb2.ErrorEvent(code=code)
    if detail is not None:
        error.detail = detail
    return chat_pb2.ChatEvent(error=error)


def chat_event_to_pb(event: ChatEvent) -> chat_pb2.ChatEvent:
    match event:
        case TokenEvent(delta=delta):
            return chat_pb2.ChatEvent(token=chat_pb2.TokenEvent(delta=delta))
        case ToolCallEvent(name=name, args=args):
            return chat_pb2.ChatEvent(
                tool_call=chat_pb2.ToolCallEvent(
                    name=name,
                    args=to_struct(args),
                )
            )
        case ToolResultEvent(name=name, text=text, attachments=attachments, error=error):
            pb = chat_pb2.ToolResultEvent(
                name=name,
                text=text,
                attachments=[attachment_to_pb(a) for a in attachments],
            )
            if error is not None:
                pb.error = redact_for_user(error, source="tool_result_error")
            return chat_pb2.ChatEvent(tool_result=pb)
        case ErrorEvent(code=code, detail=detail):
            redacted = redact_for_user(detail, source="error_event") if detail else detail
            return error_event(code, redacted)
        case DoneEvent(final_text=final_text):
            return chat_pb2.ChatEvent(done=chat_pb2.DoneEvent(final_text=final_text))
        case _:
            return error_event("unknown_event", type(event).__name__)


def final_text_for_done_frame(final_text: str, streamed_text: str) -> str:
    """Drop redundant final_text у DoneEvent якщо клієнт уже отримав його через
    delta-stream — економимо байти на wire."""
    if streamed_text and final_text == streamed_text:
        return ""
    return final_text


def codex_usage_to_pb(u: CodexUsage) -> chat_pb2.CodexUsage:
    msg = chat_pb2.CodexUsage()
    if u.plan_type:
        msg.plan_type = u.plan_type
    if u.primary is not None:
        msg.primary.CopyFrom(_usage_window_to_pb(u.primary))
    if u.secondary is not None:
        msg.secondary.CopyFrom(_usage_window_to_pb(u.secondary))
    return msg


def _usage_window_to_pb(w: UsageWindow) -> chat_pb2.UsageWindow:
    msg = chat_pb2.UsageWindow(used_percent=w.used_percent, window_minutes=w.window_minutes)
    if w.resets_at is not None:
        msg.resets_at.CopyFrom(to_ts(w.resets_at))
    return msg
