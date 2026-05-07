"""Persist media files referenced in Codex final_text into S3 + uploads table.

Codex CLI's `image_generation` tool writes PNGs to ~/.codex/generated_images
and embeds them as `![alt](path)` in the assistant final_text. After the turn
completes we copy each referenced local file into S3 and record an `uploads`
row so history replay / web client can serve them via presigned URLs.

Remote URLs (http/https) and unresolved/untrusted paths are skipped silently.
"""

import mimetypes
import uuid
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Upload
from app.services.storage.default import storage_service
from app.tg.output import AudioChunk, PhotoChunk, parse_final_text, resolve_trusted_local_path

log = structlog.get_logger(__name__)


class UploadService:
    async def persist_codex_outputs(
        self,
        session: AsyncSession,
        chat_id: int,
        final_text: str,
    ) -> list[int]:
        """Upload local media referenced in `final_text` to S3, return new upload ids."""
        if not final_text:
            return []

        paths = self._collect_local_media_paths(final_text)
        if not paths:
            return []

        upload_ids: list[int] = []
        for path in paths:
            try:
                upload = await self._persist_one(session, chat_id, path)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "uploads_persist_failed",
                    chat_id=chat_id,
                    path=str(path),
                    error=str(exc),
                )
                continue
            upload_ids.append(upload.id)

        return upload_ids

    @staticmethod
    async def _persist_one(session: AsyncSession, chat_id: int, source: Path) -> Upload:
        size = source.stat().st_size
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        key = f"chats/{chat_id}/uploads/{uuid.uuid4().hex}{source.suffix}"
        s3_path = await storage_service.upload_file(key, source, mime)

        upload = Upload(
            chat_id=chat_id,
            filename=source.name,
            mime=mime,
            size=size,
            s3_path=s3_path,
        )
        session.add(upload)
        await session.flush()
        log.info("uploads_persisted", chat_id=chat_id, upload_id=upload.id, key=key, size=size)
        return upload

    @staticmethod
    def _collect_local_media_paths(final_text: str) -> list[Path]:
        seen: set[Path] = set()
        out: list[Path] = []
        for chunk in parse_final_text(final_text):
            if not isinstance(chunk, (PhotoChunk, AudioChunk)):
                continue
            path = resolve_trusted_local_path(chunk.src)
            if path is None or path in seen:
                continue
            seen.add(path)
            out.append(path)
        return out
