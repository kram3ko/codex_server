"""JWT issue / validate + Argon2id password hashing.

Pure business logic — без HTTP/RPC/DB. DB-лукапи робить caller (AuthRPC).
"""

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import Settings


class InvalidCredentials(Exception):
    """Email невідомий або пароль неправильний."""


class InvalidToken(Exception):
    """JWT не валідний (підпис, exp, payload)."""


class AuthService:
    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        self._hasher = PasswordHasher()

    # --- passwords ---------------------------------------------------------

    def hash_password(self, plain: str) -> str:
        return self._hasher.hash(plain)

    def verify_password(self, plain: str, stored_hash: str | None) -> bool:
        if not stored_hash:
            return False
        try:
            self._hasher.verify(stored_hash, plain)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False

    def needs_rehash(self, stored_hash: str | None) -> bool:
        if not stored_hash:
            return False
        return self._hasher.check_needs_rehash(stored_hash)

    # --- tokens ------------------------------------------------------------

    def issue_token(self, subject: str) -> tuple[str, int]:
        """Видає JWT для вже-аутентифікованого subject (email). Повертає
        (jwt, expires_in_seconds)."""
        ttl = timedelta(hours=self._cfg.JWT_TTL_HOURS)
        now = datetime.now(UTC)
        payload = {
            "sub": subject,
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
        }
        encoded = jwt.encode(payload, self._cfg.JWT_SECRET, algorithm=self._cfg.JWT_ALGORITHM)
        return encoded, int(ttl.total_seconds())

    def validate_token(self, raw: str) -> str:
        """Перевіряє JWT, повертає subject (email). Кидає InvalidToken."""
        try:
            payload = jwt.decode(
                raw,
                self._cfg.JWT_SECRET,
                algorithms=[self._cfg.JWT_ALGORITHM],
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc
        sub = payload.get("sub")
        if not sub:
            raise InvalidToken("missing subject")
        return sub
