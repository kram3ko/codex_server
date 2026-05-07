"""UploadsService — chunked upload, presigned URL, list, delete."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import override

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import common_pb2, uploads_pb2
from app.grpc_generated.codex.v1.uploads_connect import UploadsService as UploadsProtocol
from app.rpc._auth import require_user
from app.rpc._mappers import to_ts, upload_to_pb
from app.services.uploads.default import upload_service

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_DEFAULT_PRESIGNED_TTL_S = 3600
_MAX_PRESIGNED_TTL_S = 24 * 3600


class UploadsRPC(UploadsProtocol):
    @override
    async def upload(  # client-streaming
        self,
        request: AsyncIterator[uploads_pb2.UploadChunk],
        ctx: RequestContext,
    ) -> uploads_pb2.UploadResponse:
        await require_user(ctx)
        init = await _consume_init(request)

        async def _data_iter() -> AsyncIterator[bytes]:
            async for chunk in request:
                payload = chunk.WhichOneof("payload")
                if payload == "data":
                    yield chunk.data
                elif payload == "init":
                    raise ConnectError(
                        Code.INVALID_ARGUMENT,
                        "duplicate `init` frame in upload stream",
                    )

        async with SessionLocal() as db:
            upload = await upload_service.persist_chunks(
                db,
                chat_id=init.chat_id if init.HasField("chat_id") else None,
                filename=init.filename,
                mime=init.mime,
                chunks=_data_iter(),
            )
            await db.commit()
            await db.refresh(upload)

        ttl_s = _DEFAULT_PRESIGNED_TTL_S
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
        await require_user(ctx)
        ttl_s = _resolve_ttl(request.ttl_seconds)
        target = request.WhichOneof("target")
        if target == "upload_id":
            async with SessionLocal() as db:
                upload = await upload_service.get(db, request.upload_id)
            if upload is None:
                raise ConnectError(Code.NOT_FOUND, f"upload {request.upload_id} not found")
            url = await upload_service.presigned_for_upload(upload, ttl_s=ttl_s)
        elif target == "source":
            url = await upload_service.presigned_for_source(request.source, ttl_s=ttl_s)
        else:
            raise ConnectError(Code.INVALID_ARGUMENT, "must provide upload_id or source")

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
        await require_user(ctx)
        limit, offset = _resolve_page(request.pagination)
        chat_id = request.chat_id if request.HasField("chat_id") else None
        async with SessionLocal() as db:
            uploads = await upload_service.list(
                db, chat_id=chat_id, limit=limit, offset=offset,
            )
        return uploads_pb2.ListUploadsResponse(uploads=[upload_to_pb(u) for u in uploads])

    @override
    async def delete_upload(
        self,
        request: uploads_pb2.DeleteUploadRequest,
        ctx: RequestContext,
    ) -> common_pb2.Empty:
        await require_user(ctx)
        async with SessionLocal() as db:
            removed = await upload_service.delete(db, request.upload_id)
            if not removed:
                raise ConnectError(Code.NOT_FOUND, f"upload {request.upload_id} not found")
            await db.commit()
        return common_pb2.Empty()


async def _consume_init(stream: AsyncIterator[uploads_pb2.UploadChunk]) -> uploads_pb2.UploadInit:
    """Перший фрейм має нести `init` з метадатою. Далі — лише `data`."""
    async for chunk in stream:
        payload = chunk.WhichOneof("payload")
        if payload != "init":
            raise ConnectError(
                Code.INVALID_ARGUMENT,
                "first frame must be `init` with filename/mime",
            )
        if not chunk.init.filename or not chunk.init.mime:
            raise ConnectError(Code.INVALID_ARGUMENT, "init.filename and init.mime required")
        return chunk.init
    raise ConnectError(Code.INVALID_ARGUMENT, "empty upload stream")


def _resolve_ttl(seconds: int) -> int:
    if seconds <= 0:
        return _DEFAULT_PRESIGNED_TTL_S
    return min(seconds, _MAX_PRESIGNED_TTL_S)


def _resolve_page(p: common_pb2.Pagination) -> tuple[int, int]:
    limit = p.limit if p.limit > 0 else _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT), max(p.offset, 0)
