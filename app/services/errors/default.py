"""Process-wide BugsinkClient singleton."""

from app.config import settings
from app.services.errors.bugsink_client import BugsinkClient

bugsink_client = BugsinkClient(
    base_url=settings.BUGSINK_INTERNAL_URL,
    auth_token=settings.BUGSINK_AUTH_TOKEN,
)
