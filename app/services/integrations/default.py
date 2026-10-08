from app.config import settings
from app.services.integrations.crypto import SecretCipher
from app.services.integrations.service import IntegrationService

integration_service = IntegrationService(SecretCipher(settings.INTEGRATIONS_KEY_FILE))
