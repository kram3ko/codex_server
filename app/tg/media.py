"""Telegram media preparation for Codex turns.

Supports text, photos, image documents, voice/audio transcription, and
video notes (кружечки) — їх аудіодоріжка транскрибується як voice.

Media bytes — і зображення, і голос — стрімяться в пам'яті без disk-buffer:
з aiogram у `BytesIO`, далі паралельно у MinIO (для історії) та у відповідний
канал (codex `data:` URI / STT). У БД зберігаємо тільки `upload_id`.
"""

import base64
import mimetypes
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path
from typing import Any

import structlog
from aiogram.types import Message
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.db.base import SessionLocal
from app.services.stt.base import STTBackend
from app.services.uploads.default import upload_service, workspace_uploads

log = structlog.get_logger(__name__)


class PreparedTurn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(description="Текст (з caption/text або транскрипт).")
    attachments: tuple[str, ...] = Field(description="`data:` URIs для зображень → codex.")
    upload_ids: tuple[int, ...] = Field(
        description="MinIO uploads.id (зберігається у message.meta)."
    )
    had_voice_input: bool = Field(
        default=False, description="True якщо текст з voice/video_note STT."
    )


async def prepare_turn(
    message: Message,
    transcriber: STTBackend,
    *,
    db_user_id: int,
    db_chat_id: int,
) -> PreparedTurn:
    if message.chat is None:
        return PreparedTurn(text="", attachments=(), upload_ids=())

    text = (message.caption or message.text or "").strip()
    attachments: list[str] = []
    upload_ids: list[int] = []
    file_paths: list[str] = []
    had_voice_input = message.voice is not None or message.video_note is not None

    if message.photo:
        data_url, upload_id = await _save_image(
            message, message.photo[-1], "image/jpeg", db_user_id, db_chat_id
        )
        attachments.append(data_url)
        upload_ids.append(upload_id)

    document = message.document
    if document is not None:
        doc_text, doc_attachment, doc_upload_id, doc_file_path = await _prepare_document(
            message, document, transcriber, db_user_id, db_chat_id
        )
        text = _merge_text(text, doc_text)
        if doc_attachment is not None:
            attachments.append(doc_attachment)
        if doc_upload_id is not None:
            upload_ids.append(doc_upload_id)
        if doc_file_path is not None:
            file_paths.append(doc_file_path)

    if message.voice:
        voice_text, upload_id = await _transcribe_and_persist(
            message,
            message.voice,
            transcriber,
            "audio/ogg",
            "voice.ogg",
            db_user_id,
            db_chat_id,
        )
        text = _merge_text(text, voice_text, label="Voice transcript")
        if upload_id is not None:
            upload_ids.append(upload_id)

    if message.video_note:
        note_text, upload_id = await _transcribe_and_persist(
            message,
            message.video_note,
            transcriber,
            "video/mp4",
            "video_note.mp4",
            db_user_id,
            db_chat_id,
        )
        text = _merge_text(text, note_text, label="Voice transcript")
        if upload_id is not None:
            upload_ids.append(upload_id)

    if message.audio:
        mime = message.audio.mime_type or "audio/mpeg"
        ext = _guess_ext(mime, message.audio.file_name, ".mp3")
        audio_text, upload_id = await _transcribe_and_persist(
            message,
            message.audio,
            transcriber,
            mime,
            f"audio{ext}",
            db_user_id,
            db_chat_id,
        )
        text = _merge_text(text, audio_text, label="Audio transcript")
        if upload_id is not None:
            upload_ids.append(upload_id)

    if (attachments or file_paths) and not text:
        text = "Open the attached files and answer based on them."
    mentions = workspace_uploads.format_mentions(file_paths)
    if mentions:
        text = f"{text}\n\n{mentions}" if text else mentions

    return PreparedTurn(
        text=text,
        attachments=tuple(attachments),
        upload_ids=tuple(upload_ids),
        had_voice_input=had_voice_input,
    )


async def _save_image(
    message: Message,
    media: Any,
    mime: str,
    user_id: int,
    chat_id: int,
) -> tuple[str, int]:
    """Download → upload to MinIO + return (data URI, upload_id)."""
    buf = await _download_to_buf(message, media)
    upload_id = await _persist_buf(
        buf,
        filename=_default_filename(mime),
        mime=mime,
        user_id=user_id,
        chat_id=chat_id,
    )
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:{mime};base64,{b64}", upload_id


async def _transcribe_and_persist(
    message: Message,
    media: Any,
    transcriber: STTBackend,
    mime: str,
    filename: str,
    user_id: int,
    chat_id: int,
) -> tuple[str, int | None]:
    """Download → upload to MinIO + transcribe; both from the same BytesIO.

    Returns (transcript, upload_id). `upload_id` may be None if MinIO push fails —
    we still want the transcript even if persistence flopped."""
    buf = await _download_to_buf(message, media)
    try:
        upload_id = await _persist_buf(
            buf, filename=filename, mime=mime, user_id=user_id, chat_id=chat_id
        )
    except (OSError, SQLAlchemyError, BotoCoreError, ClientError) as exc:
        log.warning("tg_media_persist_failed", filename=filename, error=str(exc))
        upload_id = None
    transcript = await transcriber.transcribe(buf, filename)
    return transcript, upload_id


async def _prepare_document(
    message: Message,
    document: Any,
    transcriber: STTBackend,
    user_id: int,
    chat_id: int,
) -> tuple[str, str | None, int | None, str | None]:
    """Returns ``(text, image_data_url, upload_id, workspace_path)``.

    Image documents → data URI. Audio → transcript у ``text``. Решта (PDF/DOCX/
    code/архіви) — пишемо у workspace і повертаємо relative path; caller
    додає mention у user-текст, Codex читає через свій shell tool.
    """
    mime_type = getattr(document, "mime_type", None)
    file_name = getattr(document, "file_name", None)
    if is_image_document(mime_type, file_name):
        mime = mime_type or (mimetypes.guess_type(file_name or "")[0] or "image/jpeg")
        data_url, upload_id = await _save_image(message, document, mime, user_id, chat_id)
        return "", data_url, upload_id, None
    if is_audio_document(mime_type, file_name):
        mime = mime_type or (mimetypes.guess_type(file_name or "")[0] or "audio/mpeg")
        text, upload_id = await _transcribe_and_persist(
            message,
            document,
            transcriber,
            mime,
            file_name or f"document{_guess_ext(mime, None, '.mp3')}",
            user_id,
            chat_id,
        )
        return text, None, upload_id, None
    mime = mime_type or (mimetypes.guess_type(file_name or "")[0] or "application/octet-stream")
    filename = file_name or f"document{_guess_ext(mime, None, '.bin')}"
    buf = await _download_to_buf(message, document)
    upload_id = await _persist_buf(
        buf, filename=filename, mime=mime, user_id=user_id, chat_id=chat_id
    )
    workspace_path = await workspace_uploads.materialize_for_chat(
        buf, chat_id=chat_id, upload_id=upload_id, filename=filename
    )
    return "", None, upload_id, workspace_path


async def _download_to_buf(message: Message, media: Any) -> BytesIO:
    bot = message.bot
    if bot is None:
        raise RuntimeError("aiogram Message without bot context")
    buf = BytesIO()
    await bot.download(media, destination=buf)
    buf.seek(0)
    return buf


async def _persist_buf(
    buf: BytesIO,
    *,
    filename: str,
    mime: str,
    user_id: int,
    chat_id: int,
) -> int:
    """Push BytesIO to MinIO via upload_service; returns Upload.id."""
    data = buf.getvalue()

    async def _one_chunk() -> AsyncIterator[bytes]:
        yield data

    async with SessionLocal() as db:
        upload = await upload_service.persist_chunks(
            db,
            user_id=user_id,
            chat_id=chat_id,
            filename=filename,
            mime=mime,
            chunks=_one_chunk(),
        )
        await db.commit()
        await db.refresh(upload)
    return upload.id


def _default_filename(mime: str) -> str:
    ext = mimetypes.guess_extension(mime) or ".bin"
    return f"tg_media{ext}"


def _merge_text(existing: str, incoming: str, *, label: str | None = None) -> str:
    incoming = incoming.strip()
    if not incoming:
        return existing
    if label:
        incoming = f"{label}:\n{incoming}"
    if existing:
        return f"{existing}\n\n{incoming}"
    return incoming


def is_image_document(mime_type: str | None, file_name: str | None) -> bool:
    if mime_type and mime_type.startswith("image/"):
        return True
    if file_name:
        guessed, _ = mimetypes.guess_type(file_name)
        return bool(guessed and guessed.startswith("image/"))
    return False


def is_audio_document(mime_type: str | None, file_name: str | None) -> bool:
    if mime_type and mime_type.startswith("audio/"):
        return True
    if file_name:
        guessed, _ = mimetypes.guess_type(file_name)
        return bool(guessed and guessed.startswith("audio/"))
    return False


def _guess_ext(mime_type: str | None, file_name: str | None, default: str) -> str:
    if file_name:
        suffix = Path(file_name).suffix
        if suffix:
            return suffix
    if mime_type:
        ext = mimetypes.guess_extension(mime_type)
        if ext:
            return ext
    return default
