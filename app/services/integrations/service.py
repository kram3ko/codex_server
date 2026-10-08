import asyncio
import base64
import hashlib
import secrets
from urllib.parse import urlparse

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_ssh_private_key,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration
from app.services.integrations.crypto import SecretCipher
from app.services.integrations.schemas import (
    IntegrationInput,
    IntegrationKind,
    IntegrationView,
    TelegramOptions,
)
from app.services.ssh_vault._common import resolve_user, validate_host, validate_name


class IntegrationService:
    def __init__(self, cipher: SecretCipher) -> None:
        self.cipher = cipher

    async def list(self, db: AsyncSession) -> list[Integration]:
        return list((await db.scalars(select(Integration).order_by(Integration.name))).all())

    def view(self, row: Integration) -> IntegrationView:
        return IntegrationView(
            id=row.id,
            name=row.name,
            kind=IntegrationKind(row.kind),
            host=row.host,
            username=row.username,
            enabled=row.enabled,
            has_secret=bool(row.secret),
            created_at=row.created_at,
            updated_at=row.updated_at,
            telegram=TelegramOptions.model_validate(row.options.get("telegram", {})),
            fingerprint=row.options.get("fingerprint"),
            bot_username=row.options.get("bot_username"),
        )

    async def save(
        self, db: AsyncSession, data: IntegrationInput, entry_id: int | None
    ) -> Integration:
        await db.execute(text("SELECT pg_advisory_xact_lock(6241831)"))
        row = await db.get(Integration, entry_id) if entry_id is not None else None
        if entry_id is not None and row is None:
            raise ValueError("Integration not found")
        name = data.name.strip()
        if not name:
            raise ValueError("Name is required")
        existing = await self.list(db)
        if any(item.id != entry_id and item.name == name for item in existing):
            raise ValueError("This name is already in use")
        if row is not None and row.kind != data.kind:
            raise ValueError("Integration type cannot be changed")
        if data.kind == IntegrationKind.TELEGRAM and any(
            item.kind == data.kind and item.id != entry_id for item in existing
        ):
            raise ValueError("Only one Telegram bot is supported")
        host = "" if data.kind == IntegrationKind.TELEGRAM else validate_host(data.host.strip())
        username = data.username.strip()
        if data.kind == IntegrationKind.SSH:
            validate_name(name)
            username = resolve_user(username, "git")
        if data.kind in (IntegrationKind.GITHUB, IntegrationKind.GITLAB) and any(
            item.id != entry_id and item.kind == data.kind and item.host == host
            for item in existing
        ):
            raise ValueError("A connection for this provider and host already exists")
        value = data.secret.get_secret_value().strip() if data.secret is not None else None
        if value == "" or (row is None and value is None):
            raise ValueError("Secret is required")
        verified_username = (
            await self.validate_secret(data.kind, value) if value is not None else None
        )
        options = dict(row.options) if row else {}
        if data.kind == IntegrationKind.SSH and value is not None:
            options["fingerprint"] = await asyncio.to_thread(self._fingerprint, value)
        if data.kind == IntegrationKind.TELEGRAM:
            if value is not None:
                options["bot_username"] = verified_username
            url = data.telegram.webhook_url
            if url and (urlparse(url).scheme != "https" or not urlparse(url).hostname):
                raise ValueError("Webhook URL must use HTTPS")
            options["telegram"] = data.telegram.model_dump()
            if "webhook_secret" not in options:
                options["webhook_secret"] = await asyncio.to_thread(
                    self.cipher.seal, secrets.token_urlsafe(32)
                )
        if row is None:
            row = Integration(kind=data.kind)
            db.add(row)
        row.name, row.host, row.username = name, host, username
        row.enabled, row.options = data.enabled, options
        if value is not None:
            row.secret = await asyncio.to_thread(self.cipher.seal, value)
        await db.flush()
        await db.refresh(row)
        return row

    @staticmethod
    def _fingerprint(value: str) -> str:
        loader = load_ssh_private_key if "BEGIN OPENSSH" in value else load_pem_private_key
        key = loader(value.encode(), password=None)
        public = key.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
        digest = hashlib.sha256(base64.b64decode(public.split()[1])).digest()
        return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")

    @staticmethod
    async def validate_secret(kind: IntegrationKind, value: str) -> str | None:
        if kind == IntegrationKind.SSH:
            loader = load_ssh_private_key if "BEGIN OPENSSH" in value else load_pem_private_key
            try:
                await asyncio.to_thread(loader, value.encode(), password=None)
            except (ValueError, TypeError) as exc:
                raise ValueError("An unencrypted valid SSH private key is required") from exc
        elif kind == IntegrationKind.TELEGRAM:
            bot = Bot(value)
            try:
                me = await bot.get_me()
                return me.username
            except TelegramAPIError as exc:
                raise ValueError("Telegram could not verify this token") from exc
            finally:
                await bot.session.close()
        return None

    async def check(self, row: Integration) -> str:
        value = await asyncio.to_thread(self.cipher.unseal, row.secret)
        kind = IntegrationKind(row.kind)
        if kind in (IntegrationKind.SSH, IntegrationKind.TELEGRAM):
            await self.validate_secret(kind, value)
            return (
                "Key format verified; server access not tested"
                if kind == IntegrationKind.SSH
                else "Telegram token verified"
            )
        host = "api.github.com" if row.host == "github.com" else row.host
        path = "/user" if row.host == "github.com" else "/api/v3/user"
        headers = {"Authorization": f"Bearer {value}"}
        if kind == IntegrationKind.GITLAB:
            path = "/api/v4/user"
            headers = {"PRIVATE-TOKEN": value}
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(f"https://{host}{path}", headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(
                "Provider connection failed; check host, token and permissions"
            ) from exc
        return "Provider connection verified"
