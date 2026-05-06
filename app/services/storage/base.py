"""Storage backend contract — abstraction over MinIO/S3, Dropbox, GDrive..."""

from pathlib import Path
from typing import Protocol


class StorageError(RuntimeError):
    pass


class StorageBackend(Protocol):
    """Mostly upload-only for now; presign/delete come later when web upload lands."""

    @property
    def kind(self) -> str: ...

    async def upload_file(self, key: str, source: Path, content_type: str) -> str:
        """Upload `source` to `key`, return canonical storage path/URL."""
        ...

    async def presigned_url(self, key: str, expires_s: int = 3600) -> str: ...

    async def delete(self, key: str) -> None: ...
