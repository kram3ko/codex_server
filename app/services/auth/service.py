"""JWT issue / validate. Без HTTP/RPC обвʼязки — лише бізнес-логіка."""

from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings


class InvalidCredentials(Exception):
    """WEB_API_TOKEN не співпадає з очікуваним."""


class InvalidToken(Exception):
    """JWT не валідний (підпис, exp, payload)."""


class AuthService:
    """Single-user JWT: WEB_API_TOKEN → access_token (HS256)."""

    SUBJECT = "codex-user"

    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg

    def issue_token(self, provided_password: str) -> tuple[str, int]:
        """Повертає (jwt, expires_in_seconds). Кидає InvalidCredentials."""
        if not self._cfg.WEB_API_TOKEN or provided_password != self._cfg.WEB_API_TOKEN:
            raise InvalidCredentials("invalid token")

        ttl = timedelta(hours=self._cfg.JWT_TTL_HOURS)
        now = datetime.now(UTC)
        payload = {
            "sub": self.SUBJECT,
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
        }
        encoded = jwt.encode(payload, self._cfg.JWT_SECRET, algorithm=self._cfg.JWT_ALGORITHM)
        return encoded, int(ttl.total_seconds())

    def validate_token(self, raw: str) -> str:
        """Перевіряє JWT, повертає subject. Кидає InvalidToken."""
        try:
            payload = jwt.decode(
                raw,
                self._cfg.JWT_SECRET,
                algorithms=[self._cfg.JWT_ALGORITHM],
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc

        sub = payload.get("sub")
        if sub != self.SUBJECT:
            raise InvalidToken("subject mismatch")
        return sub
