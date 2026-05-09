"""Telegram media preparation for Codex turns.

Supports text, photos, image documents, voice/audio transcription, and
video notes (кружечки) — їх аудіодоріжка транскрибується як voice.
"""

import contextlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from aiogram.types import Message

from app.config import settings
from app.services.stt.base import STTBackend

log = structlog.get_logger(__name__)

TG_UPLOADS_DIR = Path(settings.CODEX_CWD) / "tg_uploads"


@dataclass(slots=True)
class PreparedTurn:
    text: str
    attachments: tuple[str, ...]
    cleanup_paths: tuple[str, ...]
    had_voice_input: bool = False


async def prepare_turn(message: Message, transcriber: STTBackend) -> PreparedTurn:
    if message.chat is None:
        return PreparedTurn(text="", attachments=(), cleanup_paths=())

    TG_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    text = (message.caption or message.text or "").strip()
    attachments: list[str] = []
    cleanup_paths: list[str] = []
    had_voice_input = message.voice is not None or message.video_note is not None

    try:
        if message.photo:
            image_path = await _download_media(message, message.photo[-1], ".jpg")
            attachments.append(str(image_path))
            cleanup_paths.append(str(image_path))

        document = message.document
        if document is not None:
            doc_text, doc_attachment = await _prepare_document(message, document, transcriber)
            text = _merge_text(text, doc_text)
            if doc_attachment is not None:
                attachments.append(str(doc_attachment))
                cleanup_paths.append(str(doc_attachment))

        if message.voice:
            voice_text = await _transcribe_media(message, message.voice, transcriber, ".ogg")
            text = _merge_text(text, voice_text, label="Voice transcript")

        if message.video_note:
            note_text = await _transcribe_media(
                message,
                message.video_note,
                transcriber,
                ".mp4",
            )
            text = _merge_text(text, note_text, label="Voice transcript")

        if message.audio:
            audio_text = await _transcribe_media(
                message,
                message.audio,
                transcriber,
                _guess_ext(message.audio.mime_type, message.audio.file_name, ".mp3"),
            )
            text = _merge_text(text, audio_text, label="Audio transcript")
    except Exception:
        await cleanup_attachments(tuple(cleanup_paths))
        raise

    if attachments and not text:
        text = "Опиши зображення і виділи ключові деталі."

    return PreparedTurn(
        text=text,
        attachments=tuple(attachments),
        cleanup_paths=tuple(cleanup_paths),
        had_voice_input=had_voice_input,
    )


async def cleanup_attachments(attachments: tuple[str, ...]) -> None:
    for attachment in attachments:
        path = Path(attachment)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        with contextlib.suppress(OSError):
            path.parent.rmdir()


async def _prepare_document(
    message: Message,
    document: Any,
    transcriber: STTBackend,
) -> tuple[str, Path | None]:
    mime_type = getattr(document, "mime_type", None)
    file_name = getattr(document, "file_name", None)
    if is_image_document(mime_type, file_name):
        path = await _download_media(message, document, _guess_ext(mime_type, file_name, ".jpg"))
        return "", path
    if is_audio_document(mime_type, file_name):
        text = await _transcribe_media(
            message,
            document,
            transcriber,
            _guess_ext(mime_type, file_name, ".mp3"),
        )
        return text, None
    return "", None


async def _transcribe_media(
    message: Message,
    media: Any,
    transcriber: STTBackend,
    ext: str,
) -> str:
    path = await _download_media(message, media, ext)
    try:
        return await transcriber.transcribe(path)
    finally:
        with contextlib.suppress(Exception):
            path.unlink()


async def _download_media(message: Message, media: Any, ext: str) -> Path:
    bot = message.bot
    if bot is None:
        raise RuntimeError("aiogram Message without bot context")
    path = TG_UPLOADS_DIR / f"tg_{message.chat.id}_{message.message_id}_{uuid4().hex}{ext}"
    await bot.download(media, destination=path)
    return path


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
