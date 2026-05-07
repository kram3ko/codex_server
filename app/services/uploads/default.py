"""Process-wide UploadService singleton."""

from app.services.uploads.service import UploadService

upload_service = UploadService()
