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

    async def presigned_url(self, key: str, expires_s: int = 3600, *, public: bool = True) -> str:
        """Generate a time-limited GET URL.

        `public=True` (default) → host suitable for the browser (external port);
        `public=False` → internal Docker DNS, for consumers inside the network.
        """
        ...

    async def download_bytes(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...
