"""Outgoing helpers for Telegram: send_text + send_attachment.

Attachments приходять структурно через `ToolResultEvent.attachments:
tuple[Attachment, ...]` — markdown-парсинг тут не потрібен, рендер шле
бінарне фото/аудіо/файл напряму.
"""

from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.types import FSInputFile, URLInputFile

from app.config import settings
from app.services.codex.events import Attachment, AttachmentKind
from app.tg.formatting import split_tg_message, tg_html

_WORKSPACE_ROOT = Path(settings.CODEX_CWD).resolve()
# Codex CLI пише image_generation у ~/.codex/generated_images/. Mountвимо
# як trusted root окремо — інші системні шляхи блокуються.
_TRUSTED_OUTPUT_ROOTS: tuple[Path, ...] = (
    _WORKSPACE_ROOT,
    Path("/home/codex/.codex/generated_images").resolve(),
)


async def send_text(bot: Bot, chat_id: int, text: str) -> None:
    for piece in split_tg_message(text):
        await bot.send_message(chat_id=chat_id, text=tg_html(piece))


async def send_attachment(bot: Bot, chat_id: int, attachment: Attachment) -> None:
    file = _resolve_input_file(attachment.source)
    caption = tg_html(attachment.caption) if attachment.caption else None
    match attachment.kind:
        case AttachmentKind.IMAGE:
            await bot.send_photo(chat_id=chat_id, photo=file, caption=caption)
        case AttachmentKind.AUDIO:
            await bot.send_audio(chat_id=chat_id, audio=file, caption=caption)
        case AttachmentKind.FILE:
            await bot.send_document(chat_id=chat_id, document=file, caption=caption)


def resolve_trusted_local_path(source: str) -> Path | None:
    """Resolve a `source` to a local file under trusted roots, else None.
    Remote URLs (http/https) and untrusted paths return None.
    """
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return None
    path = Path(source).expanduser()
    path = (_WORKSPACE_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not _is_inside_workspace(path) or not path.is_file():
        return None
    return path


def _resolve_input_file(source: str) -> FSInputFile | URLInputFile:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return URLInputFile(source)
    path = resolve_trusted_local_path(source)
    if path is None:
        # Якщо тулза дала шлях поза trusted root — це баг, не фоллбек'имо.
        raise ValueError(f"untrusted attachment source: {source}")
    return FSInputFile(str(path))


def _is_inside_workspace(path: Path) -> bool:
    return any(path.is_relative_to(root) for root in _TRUSTED_OUTPUT_ROOTS)
