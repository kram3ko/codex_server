"""Process-wide storage singleton — picked by `STORAGE_BACKEND` setting."""

import structlog

from app.config import settings
from app.services.storage.base import StorageBackend, StorageError
from app.services.storage.s3 import S3Storage

log = structlog.get_logger(__name__)

# Dispatch table grows as we add Dropbox/GDrive backends.
_BACKENDS: dict[str, type[StorageBackend]] = {"s3": S3Storage}


def _build() -> StorageBackend:
    backend = settings.STORAGE_BACKEND.lower()
    cls = _BACKENDS.get(backend)
    if cls is None:
        raise StorageError(f"Unknown STORAGE_BACKEND={backend!r}")
    instance = cls()
    log.info("storage_selected", backend=instance.kind)
    return instance


storage_service: StorageBackend = _build()
