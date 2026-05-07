"""Persist tool-emitted attachments into S3 + uploads table.

Codex CLI's `image_generation` тулза пише PNG'и у ~/.codex/generated_images і
шле їх до нас як `Attachment` всередині `ToolResultEvent`. Після завершення
турну ми копіюємо кожен локальний файл у S3 і записуємо `uploads` row, щоб
history replay / web client мали presigned URL.

Remote URLs (http/https) пропускаються — вони не наш hosting.
"""

import mimetypes
import uuid
from collections.abc import Iterable
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Upload
from app.services.codex.events import Attachment
from app.services.storage.default import storage_service
from app.tg.output import resolve_trusted_local_path

log = structlog.get_logger(__name__)


class UploadService:
    async def persist_attachments(
        self,
        session: AsyncSession,
        chat_id: int,
        attachments: Iterable[Attachment],
    ) -> list[int]:
        """Upload each trusted-local attachment to S3, return new upload ids."""
        upload_ids: list[int] = []
        for path in self._unique_local_paths(attachments):
            try:
                upload = await self._persist_one(session, chat_id, path)
            except Exception as exc:  # noqa: BLE001 — окремий файл не валить турн
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
    def _unique_local_paths(attachments: Iterable[Attachment]) -> list[Path]:
        seen: set[Path] = set()
        out: list[Path] = []
        for attachment in attachments:
            path = resolve_trusted_local_path(attachment.source)
            if path is None or path in seen:
                continue
            seen.add(path)
            out.append(path)
        return out
