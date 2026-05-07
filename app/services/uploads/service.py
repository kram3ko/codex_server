"""Persist attachments + user-uploaded files into S3 + uploads table.

Two entry points:
- `persist_attachments` — sidecar tools (image_generation тощо) пишуть PNG'и
  у `~/.codex/generated_images/` і шлють шлях у `Attachment`. Ми копіюємо
  файл у S3 і записуємо `uploads` row.
- `persist_chunks` — web/TG client streams bytes (drag-drop, document) →
  buffer у tempfile → upload у S3 → `uploads` row.

Remote URLs (http/https) пропускаються — вони не наш hosting.
"""

import asyncio
import mimetypes
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Upload
from app.services.codex.events import Attachment
from app.services.storage.default import storage_service
from app.utils.paths import resolve_trusted_local_path

log = structlog.get_logger(__name__)

_S3_PREFIX = "s3://"
_DEFAULT_PRESIGNED_TTL_S = 3600


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
                upload = await self._persist_local_file(session, chat_id, path)
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

    async def persist_chunks(
        self,
        session: AsyncSession,
        *,
        chat_id: int | None,
        filename: str,
        mime: str,
        chunks: AsyncIterator[bytes],
    ) -> Upload:
        """Buffer bytes у tempfile до EOF, потім upload у S3 і записати row.

        `tmp.write` синхронний — для великих документів (10MB+) обертаємо
        у `to_thread`, інакше блокуємо event loop і всі інші turn'и простоюють.
        """
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
            tmp_path = Path(tmp.name)
            async for chunk in chunks:
                if chunk:
                    await asyncio.to_thread(tmp.write, chunk)
        try:
            return await self._persist_local_file(
                session, chat_id, tmp_path, filename=filename, mime=mime,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    async def get(self, session: AsyncSession, upload_id: int) -> Upload | None:
        return await session.get(Upload, upload_id)

    async def list(
        self,
        session: AsyncSession,
        *,
        chat_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Upload]:
        stmt = select(Upload).order_by(Upload.created_at.desc()).limit(limit).offset(offset)
        if chat_id is not None:
            stmt = stmt.where(Upload.chat_id == chat_id)
        rows = await session.execute(stmt)
        return list(rows.scalars())

    async def delete(self, session: AsyncSession, upload_id: int) -> bool:
        upload = await session.get(Upload, upload_id)
        if upload is None:
            return False
        key = _key_from_s3_path(upload.s3_path)
        if key is not None:
            try:
                await storage_service.delete(key)
            except Exception as exc:  # noqa: BLE001 — DB row однозначно дропаємо
                log.warning("uploads_s3_delete_failed", key=key, error=str(exc))
        await session.delete(upload)
        return True

    async def presigned_for_upload(
        self,
        upload: Upload,
        *,
        ttl_s: int = _DEFAULT_PRESIGNED_TTL_S,
    ) -> str:
        key = _key_from_s3_path(upload.s3_path)
        if key is None:
            raise ValueError(f"upload {upload.id} has non-S3 path: {upload.s3_path!r}")
        return await storage_service.presigned_url(key, expires_s=ttl_s)

    async def presigned_for_source(
        self,
        source: str,
        *,
        ttl_s: int = _DEFAULT_PRESIGNED_TTL_S,
    ) -> str:
        """Accept `s3://bucket/key`, голий key, або повну public URL.
        Public URL повертає сам себе — клієнт відкриє напряму."""
        if source.startswith(("http://", "https://")):
            return source
        key = _key_from_s3_path(source) or source
        return await storage_service.presigned_url(key, expires_s=ttl_s)

    @staticmethod
    async def _persist_local_file(
        session: AsyncSession,
        chat_id: int | None,
        source: Path,
        *,
        filename: str | None = None,
        mime: str | None = None,
    ) -> Upload:
        size = source.stat().st_size
        resolved_filename = filename or source.name
        resolved_mime = (
            mime
            or mimetypes.guess_type(resolved_filename)[0]
            or "application/octet-stream"
        )
        chat_segment = str(chat_id) if chat_id is not None else "shared"
        key = f"chats/{chat_segment}/uploads/{uuid.uuid4().hex}{Path(resolved_filename).suffix}"
        s3_path = await storage_service.upload_file(key, source, resolved_mime)

        upload = Upload(
            chat_id=chat_id,
            filename=resolved_filename,
            mime=resolved_mime,
            size=size,
            s3_path=s3_path,
        )
        session.add(upload)
        await session.flush()
        log.info(
            "uploads_persisted",
            chat_id=chat_id,
            upload_id=upload.id,
            key=key,
            size=size,
        )
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


def _key_from_s3_path(path: str) -> str | None:
    """`s3://bucket/some/key` → `some/key`. Non-S3 paths повертають None."""
    if not path.startswith(_S3_PREFIX):
        return None
    rest = path[len(_S3_PREFIX):]
    parts = rest.split("/", 1)
    return parts[1] if len(parts) == 2 else None
