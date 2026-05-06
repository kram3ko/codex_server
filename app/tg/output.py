"""Outgoing Telegram chunks: parse Codex final_text into text + media items.

Codex returns markdown. We pluck out `![alt](src)` as photos and
`[label](src.ext)` with image/audio extensions as media messages;
everything else stays text and is split to fit Telegram's 4096-char limit.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import structlog
from aiogram import Bot
from aiogram.types import FSInputFile, URLInputFile

from app.config import settings
from app.tg.formatting import split_tg_message, tg_html

log = structlog.get_logger(__name__)

_AUDIO_SUFFIXES = frozenset({".mp3", ".ogg", ".oga", ".m4a", ".wav", ".webm", ".mpga"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})

_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>\S+?)\)")
_LINK_RE = re.compile(r"(?<!\!)\[(?P<label>[^\]]*)\]\((?P<src>\S+?)\)")

_WORKSPACE_ROOT = Path(settings.CODEX_CWD).resolve()
# Codex CLI пише image_generation результати у ~/.codex/generated_images/.
# Пускаємо цей шлях окремо — туди пише тільки sidecar, користувацький
# `[markdown](path)` з іншого місця системи все одно блокується.
_TRUSTED_OUTPUT_ROOTS: tuple[Path, ...] = (
    _WORKSPACE_ROOT,
    Path("/home/codex/.codex/generated_images").resolve(),
)


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str


@dataclass(frozen=True, slots=True)
class PhotoChunk:
    src: str
    caption: str


@dataclass(frozen=True, slots=True)
class AudioChunk:
    src: str
    caption: str


OutgoingChunk = TextChunk | PhotoChunk | AudioChunk


def parse_final_text(final_text: str) -> list[OutgoingChunk]:
    """Split Codex output into ordered text + media chunks."""
    if not final_text:
        return []
    chunks: list[OutgoingChunk] = []
    cursor = 0
    for start, end, media in _media_spans(final_text):
        if start > cursor:
            chunks.append(TextChunk(text=final_text[cursor:start]))
        chunks.append(media)
        cursor = end
    if cursor < len(final_text):
        chunks.append(TextChunk(text=final_text[cursor:]))
    return [c for c in chunks if not (isinstance(c, TextChunk) and not c.text.strip())]


async def send_chunks(bot: Bot, chat_id: int, chunks: Iterable[OutgoingChunk]) -> None:
    for chunk in chunks:
        if isinstance(chunk, TextChunk):
            for piece in split_tg_message(chunk.text):
                await bot.send_message(chat_id=chat_id, text=tg_html(piece))
        elif isinstance(chunk, PhotoChunk):
            await _send_photo(bot, chat_id, chunk)
        elif isinstance(chunk, AudioChunk):
            await _send_audio(bot, chat_id, chunk)


def _media_spans(text: str) -> list[tuple[int, int, OutgoingChunk]]:
    spans: list[tuple[int, int, OutgoingChunk]] = []
    for m in _IMAGE_RE.finditer(text):
        spans.append((m.start(), m.end(), PhotoChunk(src=m.group("src"), caption=m.group("alt"))))
    for m in _LINK_RE.finditer(text):
        if _is_audio(m.group("src")):
            spans.append(
                (m.start(), m.end(), AudioChunk(src=m.group("src"), caption=m.group("label"))),
            )
        elif _is_image(m.group("src")):
            spans.append(
                (m.start(), m.end(), PhotoChunk(src=m.group("src"), caption=m.group("label"))),
            )
    spans.sort(key=lambda t: t[0])
    out: list[tuple[int, int, OutgoingChunk]] = []
    last_end = 0
    for start, end, chunk in spans:
        if start < last_end:
            continue
        out.append((start, end, chunk))
        last_end = end
    return out


async def _send_photo(bot: Bot, chat_id: int, chunk: PhotoChunk) -> None:
    file = _resolve_input_file(chunk.src)
    caption = tg_html(chunk.caption) if chunk.caption else None
    if file is None:
        await bot.send_message(chat_id=chat_id, text=tg_html(f"[image unavailable] {chunk.src}"))
        return
    try:
        await bot.send_photo(chat_id=chat_id, photo=file, caption=caption)
    except Exception as exc:  # noqa: BLE001
        log.warning("tg_send_photo_failed", src=chunk.src, error=str(exc))
        await bot.send_message(chat_id=chat_id, text=tg_html(f"[photo failed] {chunk.src}: {exc}"))


async def _send_audio(bot: Bot, chat_id: int, chunk: AudioChunk) -> None:
    file = _resolve_input_file(chunk.src)
    caption = tg_html(chunk.caption) if chunk.caption else None
    if file is None:
        await bot.send_message(chat_id=chat_id, text=tg_html(f"[audio unavailable] {chunk.src}"))
        return
    try:
        await bot.send_audio(chat_id=chat_id, audio=file, caption=caption)
    except Exception as exc:  # noqa: BLE001
        log.warning("tg_send_audio_failed", src=chunk.src, error=str(exc))
        await bot.send_message(chat_id=chat_id, text=tg_html(f"[audio failed] {chunk.src}: {exc}"))


def _resolve_input_file(src: str):
    parsed = urlparse(src)
    if parsed.scheme in {"http", "https"}:
        return URLInputFile(src)
    path = Path(src).expanduser()
    path = (_WORKSPACE_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not _is_inside_workspace(path):
        log.warning("tg_outgoing_path_outside_workspace", path=str(path))
        return None
    if not path.is_file():
        log.warning("tg_outgoing_path_missing", path=str(path))
        return None
    return FSInputFile(str(path))


def _is_inside_workspace(path: Path) -> bool:
    for root in _TRUSTED_OUTPUT_ROOTS:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _is_audio(src: str) -> bool:
    parsed = urlparse(src)
    candidate = parsed.path if parsed.scheme else src
    return Path(candidate).suffix.lower() in _AUDIO_SUFFIXES


def _is_image(src: str) -> bool:
    parsed = urlparse(src)
    candidate = parsed.path if parsed.scheme else src
    return Path(candidate).suffix.lower() in _IMAGE_SUFFIXES
