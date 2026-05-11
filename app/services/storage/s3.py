"""MinIO/S3 backend (aiobotocore)."""

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog
from aiobotocore.session import get_session

from app.config import settings
from app.services.storage.base import StorageError

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

log = structlog.get_logger(__name__)


class S3Storage:
    kind = "s3"

    def __init__(self) -> None:
        self._endpoint = settings.S3_ENDPOINT
        self._access_key = settings.S3_ACCESS_KEY_ID
        self._secret_key = settings.S3_SECRET_KEY
        self._bucket = settings.S3_BUCKET
        self._region = settings.S3_REGION
        self._session = get_session()

    def _client(self) -> AbstractAsyncContextManager[S3Client]:
        return cast(
            "AbstractAsyncContextManager[S3Client]",
            self._session.create_client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name=self._region,
            ),
        )

    async def upload_file(self, key: str, source: Path, content_type: str) -> str:
        async with self._client() as s3:
            with source.open("rb") as fp:
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=fp,
                    ContentType=content_type,
                )
        log.info("s3_uploaded", bucket=self._bucket, key=key, size=source.stat().st_size)
        return f"s3://{self._bucket}/{key}"

    async def presigned_url(self, key: str, expires_s: int = 3600, *, public: bool = True) -> str:
        async with self._client() as s3:
            url = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_s,
            )
        # Підпис S3 v4 не включає хост у канонічному запиті, тож swap безпечний.
        public_host = settings.S3_PUBLIC_ENDPOINT
        if public and public_host and public_host != self._endpoint:
            url = url.replace(self._endpoint, public_host, 1)
        return url

    async def download_bytes(self, key: str) -> bytes:
        async with self._client() as s3:
            obj = await s3.get_object(Bucket=self._bucket, Key=key)
            async with obj["Body"] as body:
                return await body.read()

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            try:
                await s3.delete_object(Bucket=self._bucket, Key=key)
            except Exception as exc:  # noqa: BLE001
                raise StorageError(f"S3 delete failed for {key}: {exc}") from exc
