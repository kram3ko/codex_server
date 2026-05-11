"""AuthService — argon2id hashing + JWT issue/validate."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import Settings
from app.services.auth.service import AuthService, InvalidToken


def _service(**overrides: object) -> AuthService:
    base: dict[str, object] = {
        "JWT_SECRET": "test-secret-32-bytes-of-randomness!",
        "JWT_ALGORITHM": "HS256",
        "JWT_TTL_HOURS": 1,
    }
    base.update(overrides)
    return AuthService(Settings(**base))  # type: ignore[arg-type]


def test_hash_and_verify_password_round_trip() -> None:
    svc = _service()
    hashed = svc.hash_password("hunter2")
    assert svc.verify_password("hunter2", hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    svc = _service()
    hashed = svc.hash_password("hunter2")
    assert svc.verify_password("wrong", hashed) is False


def test_verify_password_rejects_none_hash() -> None:
    """TG-only юзер (без паролю) не повинен пройти password login."""
    svc = _service()
    assert svc.verify_password("any", None) is False


def test_verify_password_rejects_malformed_hash() -> None:
    svc = _service()
    assert svc.verify_password("any", "not-a-real-hash") is False


def test_needs_rehash_handles_none() -> None:
    svc = _service()
    assert svc.needs_rehash(None) is False


def test_issue_token_round_trip_subject() -> None:
    svc = _service(JWT_TTL_HOURS=1)
    token, expires_in = svc.issue_token("user@example.com")
    assert expires_in == 3600
    assert svc.validate_token(token) == "user@example.com"


def test_validate_token_rejects_bad_signature() -> None:
    svc = _service()
    # Підписаний іншим секретом.
    forged = jwt.encode(
        {"sub": "user@example.com", "exp": datetime.now(UTC) + timedelta(hours=1)},
        "different-secret",
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        svc.validate_token(forged)


def test_validate_token_rejects_expired() -> None:
    svc = _service()
    expired = jwt.encode(
        {
            "sub": "user@example.com",
            "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        },
        svc._cfg.JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        svc.validate_token(expired)


def test_validate_token_rejects_missing_subject() -> None:
    svc = _service()
    no_sub = jwt.encode(
        {"exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp())},
        svc._cfg.JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        svc.validate_token(no_sub)


def test_validate_token_rejects_garbage() -> None:
    svc = _service()
    with pytest.raises(InvalidToken):
        svc.validate_token("not.a.jwt")
