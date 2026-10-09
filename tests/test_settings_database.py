"""integrations — sealed secrets round-trip and unique name on a real Postgres."""

from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import Integration, TelegramChat
from app.services.integrations.crypto import SecretCipher
from app.services.integrations.schemas import IntegrationInput, IntegrationKind
from app.services.integrations.service import IntegrationService

pytestmark = pytest.mark.integration


@pytest.fixture
async def database(database_engine: AsyncEngine):
    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            schema = "settings_test_" + uuid4().hex
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            for model in (Integration, TelegramChat):
                await connection.run_sync(model.__table__.create)
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                yield db
        finally:
            await transaction.rollback()


async def test_settings_create_edit_and_duplicate(database, tmp_path):
    key = tmp_path / "master.key"
    key.write_bytes(Fernet.generate_key())
    service = IntegrationService(SecretCipher(str(key)))
    data = IntegrationInput(
        name="example",
        kind=IntegrationKind.GITHUB,
        host="github.com",
        secret=SecretStr("example-token"),
    )
    row = await service.save(database, data, None)
    sealed = row.secret
    assert sealed != "example-token"
    assert service.cipher.unseal(sealed) == "example-token"
    edited = await service.save(
        database, data.model_copy(update={"secret": None, "username": "updated"}), row.id
    )
    assert edited.secret == sealed
    assert edited.username == "updated"
    with pytest.raises(ValueError, match="name"):
        await service.save(database, data, None)
    assert len(list(await database.scalars(select(Integration)))) == 1
