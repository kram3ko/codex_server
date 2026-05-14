"""Кінцеві стани turn'у — done / empty / dropped, + TG-вивід відповіді."""

import structlog
from aiogram.types import Message

from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.events import Attachment, ToolCallRecord
from app.services.sessions.store import ChatSession
from app.tg.markdown import tg_markdown
from app.tg.media import PreparedTurn
from app.tg.output import send_attachment, send_text, send_voice_reply
from app.tg.turn.control import emit_failure
from app.tg.turn.persistence import persist_assistant_turn

log = structlog.get_logger(__name__)


async def handle_done(
    session: ChatSession,
    message: Message,
    prepared: PreparedTurn,
    final_text: str,
    attachments: list[Attachment],
    tool_calls: list[ToolCallRecord],
    committed_prefix: str,
) -> None:
    if not final_text.strip() and not attachments:
        await handle_empty_response(session, message, prepared)
        return
    await send_response(
        message,
        final_text,
        attachments,
        committed_prefix,
        as_voice=prepared.had_voice_input,
    )
    await persist_assistant_turn(session, final_text, attachments, tool_calls)


async def handle_empty_response(
    session: ChatSession,
    message: Message,
    prepared: PreparedTurn,
) -> None:
    log.info(
        "tg_empty_response",
        attachments=len(prepared.attachments),
        text_len=len(prepared.text),
    )
    await message.answer(
        tg_markdown.escape(
            "Codex returned empty response. Try adding a caption or send the image again.",
        ),
    )
    await emit_failure(session, code=CodexErrorCode.EMPTY_RESPONSE, detail="no final text")


async def handle_dropped_stream(
    session: ChatSession,
    message: Message,
    prepared: PreparedTurn,
    buffer: str,
    attachments: list[Attachment],
    tool_calls: list[ToolCallRecord],
    committed_prefix: str,
) -> None:
    log.error(
        "tg_codex_stream_ended_without_done",
        chat_id=message.chat.id if message.chat else None,
        buffer_len=len(buffer),
    )
    tail = buffer.strip()
    if not tail and not attachments:
        await message.answer(
            tg_markdown.escape(
                "⚠ Codex обірвав turn до відповіді. Контекст збережено — продовжуй розмову.",
            )
        )
        await emit_failure(session, code=CodexErrorCode.STREAM_DROPPED, detail="no buffer")
        return
    await send_response(
        message,
        tail,
        attachments,
        committed_prefix,
        as_voice=prepared.had_voice_input,
    )
    await persist_assistant_turn(session, tail, attachments, tool_calls, partial=True)
    await message.answer(
        tg_markdown.escape(
            "⚠ Codex обірвав turn посеред відповіді — те що встигло, лишається. "
            "Можеш писати далі.",
        ),
    )


async def send_response(
    message: Message,
    final_text: str,
    attachments: list[Attachment],
    committed_prefix: str,
    *,
    as_voice: bool = False,
) -> None:
    # `handle()` уже відсік `message.chat is None`; bot гарантовано є.
    assert message.bot is not None and message.chat is not None
    # Voice mode: повний текст одним голосовим — під час voice-input ми не
    # стрімили бульбашки, тож committed_prefix="". TTS-fail → текст-фолбек.
    if as_voice and final_text.strip():
        sent = await send_voice_reply(message.bot, message.chat.id, final_text)
        if not sent:
            await send_text(message.bot, message.chat.id, final_text)
    else:
        remainder = strip_committed_prefix(final_text, committed_prefix)
        if remainder:
            await send_text(message.bot, message.chat.id, remainder)
    for attachment in attachments:
        await send_attachment(message.bot, message.chat.id, attachment)


def strip_committed_prefix(text: str, committed: str) -> str:
    """Return лише ту частину `text`, яка ще не була стрімлена як бульбашка.

    `committed` — strict prefix вже опублікованого асистент-тексту (через
    `progress.note_partial`). Іноді модель переписує ранні токени (рідко з
    Codex) — у такому разі prefix не співпаде, і ми свідомо повертаємо повний
    `text`: краще дублювати, ніж проковтнути final.
    """
    if not committed:
        return text
    if not text.startswith(committed):
        return text
    return text[len(committed) :].lstrip()
