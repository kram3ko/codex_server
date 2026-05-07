"""Outgoing helpers for Telegram: send_text + send_attachment + send_voice_reply.

Attachments приходять структурно через `ToolResultEvent.attachments:
tuple[Attachment, ...]` — markdown-парсинг тут не потрібен, рендер шле
бінарне фото/аудіо/файл напряму.
"""

import contextlib
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import structlog
from aiogram import Bot
from aiogram.types import FSInputFile, URLInputFile

from app.services.codex.events import Attachment, AttachmentKind
from app.services.tts.base import SpeechSynthesisError
from app.services.tts.default import tts_service
from app.tg.markdown import tg_markdown
from app.utils.paths import resolve_trusted_local_path

log = structlog.get_logger(__name__)

# OGG_OPUS — формат TG voice (`send_voice`); MP3 не підходить, треба send_audio.
_VOICE_ENCODING = "OGG_OPUS"
# /tmp (а не workspace) щоб ogg-ки не сипались у git-tracked репо.
_TTS_TMP_DIR = Path(tempfile.gettempdir()) / "codex_tts"


async def send_text(bot: Bot, chat_id: int, text: str) -> None:
    for piece in tg_markdown.render_html(text):
        await bot.send_message(chat_id=chat_id, text=piece)


async def send_voice_reply(bot: Bot, chat_id: int, text: str) -> bool:
    """Synthesize `text` via TTS, send as TG voice. Returns False on failure."""
    spoken = tg_markdown.to_plain(text)
    if not spoken or not tts_service.enabled:
        return False
    _TTS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _TTS_TMP_DIR / f"tts_{chat_id}_{uuid4().hex}.ogg"
    try:
        await tts_service.synthesize(spoken, out_path, audio_encoding=_VOICE_ENCODING)
    except SpeechSynthesisError as exc:
        log.warning("tg_tts_failed", chat_id=chat_id, error=str(exc)[:200])
        return False
    try:
        await bot.send_voice(chat_id=chat_id, voice=FSInputFile(str(out_path)))
    finally:
        with contextlib.suppress(FileNotFoundError):
            out_path.unlink()
    return True


async def send_attachment(bot: Bot, chat_id: int, attachment: Attachment) -> None:
    file = _resolve_input_file(attachment.source)
    caption = tg_markdown.escape(attachment.caption) if attachment.caption else None
    match attachment.kind:
        case AttachmentKind.IMAGE:
            await bot.send_photo(chat_id=chat_id, photo=file, caption=caption)
        case AttachmentKind.AUDIO:
            await bot.send_audio(chat_id=chat_id, audio=file, caption=caption)
        case AttachmentKind.FILE:
            await bot.send_document(chat_id=chat_id, document=file, caption=caption)


def _resolve_input_file(source: str) -> FSInputFile | URLInputFile:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return URLInputFile(source)
    path = resolve_trusted_local_path(source)
    if path is None:
        # Якщо тулза дала шлях поза trusted root — це баг, не фоллбек'имо.
        raise ValueError(f"untrusted attachment source: {source}")
    return FSInputFile(str(path))
