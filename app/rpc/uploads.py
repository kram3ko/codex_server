"""UploadsService — unary upload, presigned URL, list, delete."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import override

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.config import settings
from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import common_pb2, uploads_pb2
from app.grpc_generated.codex.v1.uploads_connect import UploadsService as UploadsProtocol
from app.rpc._auth import require_user
from app.rpc._mappers import to_ts, upload_to_pb
from app.services.stt.default import stt_service
from app.services.uploads.default import upload_service

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class UploadsRPC(UploadsProtocol):
    @override
    async def upload_once(
        self,
        request: uploads_pb2.UploadOnceRequest,
        ctx: RequestContext,
    ) -> uploads_pb2.UploadResponse:
        user = await require_user(ctx)
        if not request.filename or not request.mime:
            raise ConnectError(Code.INVALID_ARGUMENT, "filename and mime required")

        async def _data_iter() -> AsyncIterator[bytes]:
            yield request.data

        async with SessionLocal() as db:
            upload = await upload_service.persist_chunks(
                db,
                user_id=user.id,
                chat_id=request.chat_id if request.HasField("chat_id") else None,
                filename=request.filename,
                mime=request.mime,
                chunks=_data_iter(),
            )
            await db.commit()
            await db.refresh(upload)

        ttl_s = settings.S3_PRESIGNED_DEFAULT_TTL_SECONDS
        url = await upload_service.presigned_for_upload(upload, ttl_s=ttl_s)
        return uploads_pb2.UploadResponse(
            upload=upload_to_pb(upload),
            presigned_url=url,
            presigned_ttl_seconds=ttl_s,
        )

    @override
    async def get_presigned(
        self,
        request: uploads_pb2.GetPresignedRequest,
        ctx: RequestContext,
    ) -> uploads_pb2.GetPresignedResponse:
        user = await require_user(ctx)
        ttl_s = _resolve_ttl(request.ttl_seconds)
        if request.WhichOneof("target") != "upload_id":
            raise ConnectError(Code.INVALID_ARGUMENT, "upload_id required")
        async with SessionLocal() as db:
            upload = await upload_service.get(db, request.upload_id, user_id=user.id)
        if upload is None:
            raise ConnectError(Code.NOT_FOUND, f"upload {request.upload_id} not found")
        url = await upload_service.presigned_for_upload(upload, ttl_s=ttl_s)
        return uploads_pb2.GetPresignedResponse(
            url=url,
            expires_at=to_ts(datetime.now(UTC) + timedelta(seconds=ttl_s)),
        )

    @override
    async def list_uploads(
        self,
        request: uploads_pb2.ListUploadsRequest,
        ctx: RequestContext,
    ) -> uploads_pb2.ListUploadsResponse:
        user = await require_user(ctx)
        limit, offset = _resolve_page(request.pagination)
        chat_id = request.chat_id if request.HasField("chat_id") else None
        async with SessionLocal() as db:
            uploads = await upload_service.list(
                db,
                user_id=user.id,
                chat_id=chat_id,
                limit=limit,
                offset=offset,
            )
        return uploads_pb2.ListUploadsResponse(uploads=[upload_to_pb(u) for u in uploads])

    @override
    async def delete_upload(
        self,
        request: uploads_pb2.DeleteUploadRequest,
        ctx: RequestContext,
    ) -> common_pb2.Empty:
        user = await require_user(ctx)
        async with SessionLocal() as db:
            removed = await upload_service.delete(db, request.upload_id, user_id=user.id)
            if not removed:
                raise ConnectError(Code.NOT_FOUND, f"upload {request.upload_id} not found")
            await db.commit()
        return common_pb2.Empty()

    @override
    async def transcribe_upload(
        self,
        request: uploads_pb2.TranscribeUploadRequest,
        ctx: RequestContext,
    ) -> uploads_pb2.TranscribeUploadResponse:
        user = await require_user(ctx)
        async with SessionLocal() as db:
            upload = await upload_service.get(db, request.upload_id, user_id=user.id)
        if upload is None:
            raise ConnectError(Code.NOT_FOUND, f"upload {request.upload_id} not found")
        if not (upload.mime.startswith("audio/") or upload.mime.startswith("video/")):
            raise ConnectError(Code.INVALID_ARGUMENT, f"upload {upload.id} is not audio/video")
        buf = BytesIO()
        await upload_service.download_to_stream(upload, buf)
        text = await stt_service.transcribe(buf, upload.filename)
        return uploads_pb2.TranscribeUploadResponse(text=text)


def _resolve_ttl(seconds: int) -> int:
    if seconds <= 0:
        return settings.S3_PRESIGNED_DEFAULT_TTL_SECONDS
    return min(seconds, settings.S3_PRESIGNED_MAX_TTL_SECONDS)


def _resolve_page(p: common_pb2.Pagination) -> tuple[int, int]:
    limit = p.limit if p.limit > 0 else _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT), max(p.offset, 0)
