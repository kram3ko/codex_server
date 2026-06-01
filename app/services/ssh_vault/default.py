from pathlib import Path

from app.config import settings
from app.services.ssh_vault.service import SshVaultService
from app.services.ssh_vault.tokens import GitTokenService

_vault_dir = Path(settings.SSH_VAULT_DIR)

ssh_vault_service = SshVaultService(_vault_dir)
git_token_service = GitTokenService(_vault_dir)
