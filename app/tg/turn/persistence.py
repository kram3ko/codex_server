"""DB persistence для одного TG turn'у — user message → assistant message + events.

Розділено на pre-turn (`persist_user_turn`) і post-turn (`persist_assistant_turn`)
щоб stream/outcome модулі не торкались SQL напряму.
"""

from typing import Any

from app.db.base import SessionLocal
from app.models import EventKind, MessageRole
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.events import Attachment, ToolCallRecord
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.sessions.store import ChatSession
from app.services.turns.default import turn_service
from app.services.uploads.default import upload_service
from app.tg.media import PreparedTurn


async def persist_user_turn(session: ChatSession, prepared: PreparedTurn) -> int:
    """Returns USER message id — caller передає у `turn_service.create_starting`."""
    meta = {"upload_ids": list(prepared.upload_ids)} if prepared.upload_ids else None
    async with SessionLocal() as db:
        msg = await message_service.append(
            db,
            session.db_chat_id,
            MessageRole.USER,
            prepared.text,
            meta=meta,
        )
        await event_service.emit(
            db,
            EventKind.TURN_STARTED,
            chat_id=session.db_chat_id,
            user_id=session.db_user_id,
            payload={
                "text_len": len(prepared.text),
                "attachments": len(prepared.attachments),
            },
        )
        await db.commit()
        return msg.id


async def persist_assistant_turn(
    session: ChatSession,
    final_text: str,
    attachments: list[Attachment],
    tool_calls: list[ToolCallRecord],
    *,
    partial: bool = False,
    turn_id: int | None = None,
) -> None:
    async with SessionLocal() as db:
        upload_ids = await upload_service.persist_attachments(
            db,
            chat_id=session.db_chat_id,
            user_id=session.db_user_id,
            attachments=attachments,
        )
        meta = _build_assistant_meta(tool_calls, upload_ids, partial=partial)
        msg = await message_service.append(
            db,
            session.db_chat_id,
            MessageRole.ASSISTANT,
            final_text,
            meta=meta,
        )
        if turn_id is not None:
            await turn_service.attach_assistant_message(db, turn_id, msg.id)
        await event_service.emit(
            db,
            EventKind.TURN_FAILED if partial else EventKind.TURN_COMPLETED,
            chat_id=session.db_chat_id,
            user_id=session.db_user_id,
            payload=_build_turn_payload(final_text, tool_calls, upload_ids, partial=partial),
        )
        await db.commit()


def _build_assistant_meta(
    tool_calls: list[ToolCallRecord],
    upload_ids: list[int],
    *,
    partial: bool,
) -> dict[str, Any] | None:
    meta: dict[str, Any] = {}
    if partial:
        meta["partial"] = True
    if tool_calls:
        meta["calls"] = tool_calls
    if upload_ids:
        meta["upload_ids"] = upload_ids
    return meta or None


def _build_turn_payload(
    final_text: str,
    tool_calls: list[ToolCallRecord],
    upload_ids: list[int],
    *,
    partial: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "final_text_len": len(final_text),
        "tool_calls": len(tool_calls),
        "uploads": len(upload_ids),
    }
    if partial:
        payload["reason"] = CodexErrorCode.STREAM_DROPPED
    return payload
