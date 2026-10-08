from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.settings import router
from app.models import Integration
from app.services.integrations.crypto import SecretCipher
from app.services.integrations.runtime import materialize
from app.services.integrations.schemas import IntegrationInput, IntegrationKind
from app.services.integrations.service import IntegrationService


@pytest.fixture
def cipher(tmp_path: Path) -> SecretCipher:
    path = tmp_path / "master.key"
    path.write_bytes(Fernet.generate_key())
    return SecretCipher(str(path))


def record(cipher: SecretCipher, **values) -> Integration:
    return Integration(
        id=1,
        name="github",
        kind="github",
        host="github.com",
        username="user",
        secret=cipher.seal("test-token"),
        enabled=True,
        options={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        **values,
    )


def test_cipher_does_not_store_plaintext(cipher: SecretCipher) -> None:
    sealed = cipher.seal("private-token")
    assert "private-token" not in sealed
    assert cipher.unseal(sealed) == "private-token"


def test_missing_master_key_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="master key"):
        SecretCipher(str(tmp_path / "missing")).seal("token")


def test_wrong_master_key_fails_closed(cipher: SecretCipher, tmp_path: Path) -> None:
    path = tmp_path / "other.key"
    path.write_bytes(Fernet.generate_key())
    with pytest.raises(ValueError, match="decrypt"):
        SecretCipher(str(path)).unseal(cipher.seal("token"))


def test_public_view_never_contains_secret(cipher: SecretCipher) -> None:
    row = record(cipher)
    view = IntegrationService(cipher).view(row).model_dump_json()
    assert row.secret not in view
    assert "test-token" not in view


def test_runtime_replaces_credentials_on_disable(cipher: SecretCipher, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    row = record(cipher)
    materialize([row], cipher, str(root))
    assert "test-token" in (root / "git-credentials").read_text()
    assert (root / "git-credentials").stat().st_mode & 0o777 == 0o600
    row.enabled = False
    materialize([row], cipher, str(root))
    assert "test-token" not in (root / "git-credentials").read_text()
    assert (root / "cli-tokens.json").read_text() == "[]"


def test_input_secret_repr_is_redacted() -> None:
    data = IntegrationInput(
        name="test", kind=IntegrationKind.GITHUB, secret=SecretStr("secret-value")
    )
    assert "secret-value" not in repr(data)


async def test_invalid_ssh_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="SSH private key"):
        await IntegrationService.validate_secret(IntegrationKind.SSH, "PRIVATE KEY garbage")


async def test_settings_reject_unauthenticated_and_unverified_requests() -> None:
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/settings/integrations")).status_code == 401
        assert (await client.post("/api/settings/integrations", json={})).status_code == 403
