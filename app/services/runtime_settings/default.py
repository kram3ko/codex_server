from app.config import settings
from app.db.base import SessionLocal
from app.services.runtime_settings.service import RuntimeSettingsService

runtime_settings_service = RuntimeSettingsService(
    SessionLocal, ttl_s=settings.RUNTIME_SETTINGS_TTL_S
)
