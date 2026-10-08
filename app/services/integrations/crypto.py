import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    def __init__(self, key_path: str) -> None:
        self._path = Path(key_path)

    def ensure_key(self) -> bool:
        """Створює master key якщо файлу ще нема. Returns True якщо створено.
        Викликати лише коли зашифрованих записів ще нема — інакше втрачений
        ключ маскується новим і старі секрети стають нечитабельними."""
        if self._path.exists():
            Fernet(self._path.read_bytes().strip())
            return False
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(Fernet.generate_key())
            output.flush()
            os.fsync(output.fileno())
        return True

    def _cipher(self) -> Fernet:
        try:
            return Fernet(self._path.read_bytes().strip())
        except (OSError, ValueError) as exc:
            raise ValueError(
                "Integration master key is unavailable; run settings preparation"
            ) from exc

    def seal(self, value: str) -> str:
        return self._cipher().encrypt(value.encode()).decode()

    def unseal(self, value: str) -> str:
        try:
            return self._cipher().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Cannot decrypt integration with the configured master key") from exc
