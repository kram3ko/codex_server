"""Process-wide UploadService + WorkspaceUploads singletons."""

from pathlib import Path

from app.config import settings
from app.services.uploads.service import UploadService
from app.services.uploads.workspace import WorkspaceUploads

upload_service = UploadService()
workspace_uploads = WorkspaceUploads(Path(settings.CODEX_CWD))
