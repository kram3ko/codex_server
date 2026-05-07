"""Process-wide AuthService singleton bound to settings."""

from app.config import settings
from app.services.auth.service import AuthService

auth_service = AuthService(settings)
