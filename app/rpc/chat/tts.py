"""Post-turn TTS attach — синтез реплики у фоні без блокування stream'у.

`attach_tts_to_message` стартується через `asyncio.create_task` після того,
як клієнт уже отримав DoneEvent. Tempfile-hop тут навмисний: TTS backend
жадає Path. Помилки swallow'аться — TTS опціональний.
"""

import contextlib
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import flag_modified

from app.db.base import SessionLocal
from app.models import Message
from app.services.tts.base import SpeechSynthesisError
from app.services.tts.default import tts_service
from app.services.uploads.default import upload_service

log = structlog.get_logger(__name__)


async def attach_tts_to_message(
    message_id: int,
    final_text: str,
    chat_id: int,
    user_id: int,
) -> None:
    try:
        tts_upload_id = await _synthesize_reply(final_text, chat_id, user_id)
    except (OSError, SQLAlchemyError, BotoCoreError, ClientError) as exc:
        log.warning("web_tts_attach_failed", message_id=message_id, error=str(exc))
        return
    if tts_upload_id is None:
        return
    async with SessionLocal() as db:
        msg = await db.scalar(select(Message).where(Message.id == message_id))
        if msg is None:
            return
        meta = dict(msg.meta or {})
        existing = list(meta.get("audio_upload_ids", []))
        existing.append(tts_upload_id)
        meta["audio_upload_ids"] = existing
        msg.meta = meta
        flag_modified(msg, "meta")
        await db.commit()


async def _synthesize_reply(text: str, chat_id: int, user_id: int) -> int | None:
    if not tts_service.enabled:
        return None
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            await tts_service.synthesize(text, tmp_path, audio_encoding="OGG_OPUS")
        except SpeechSynthesisError as exc:
            log.warning("web_tts_failed", chat_id=chat_id, error=str(exc)[:200])
            return None
        data = tmp_path.read_bytes()
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()

    async def _one_chunk() -> AsyncIterator[bytes]:
        yield data

    async with SessionLocal() as db:
        upload = await upload_service.persist_chunks(
            db,
            user_id=user_id,
            chat_id=chat_id,
            filename="reply.ogg",
            mime="audio/ogg",
            chunks=_one_chunk(),
        )
        await db.commit()
        await db.refresh(upload)
    return upload.id
