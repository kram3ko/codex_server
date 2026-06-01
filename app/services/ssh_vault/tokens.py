"""HTTPS git tokens — пишуться у `git-credentials` у тому ж vault volume.
codex-cli's git читає їх через `credential.helper store --file`, тож агент
клонує `https://<host>/org/repo` по токену без рестарту. Секрети живуть у
`tokens.json` поряд (0600), щоб credentials-файл перегенерувати на delete.
`user`/`token` URL-encode'яться у credentials-рядку — жодних інжектів.
"""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import orjson
from pydantic import BaseModel, ConfigDict

from app.services.ssh_vault._common import (
    SshVaultError,
    resolve_user,
    validate_host,
    validate_name,
    vault_lock,
    write_secret,
)

_DEFAULT_USER = "x-access-token"
_INDEX_FILE = "tokens.json"
_CREDENTIALS_FILE = "git-credentials"


class ApiTokenMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    host: str
    user: str
    created_at: datetime


class _TokenRecord(ApiTokenMeta):
    token: str


class GitTokenService:
    def __init__(self, vault_dir: Path) -> None:
        self._dir = vault_dir

    def add(self, *, name: str, host: str, user: str, token: str) -> ApiTokenMeta:
        name = validate_name(name.strip())
        host = validate_host(host.strip())
        user = resolve_user(user.strip(), _DEFAULT_USER)
        token = token.strip()
        if not token:
            raise SshVaultError("token is required")

        with vault_lock(self._dir):
            records = self._read()
            records[name] = _TokenRecord(
                name=name, host=host, user=user, token=token, created_at=datetime.now(UTC)
            )
            self._write_index(records)
            self._write_credentials(records)
        return _public(records[name])

    def list(self) -> list[ApiTokenMeta]:
        return sorted((_public(r) for r in self._read().values()), key=lambda m: m.name)

    def delete(self, name: str) -> None:
        with vault_lock(self._dir):
            records = self._read()
            if name not in records:
                raise SshVaultError(f"unknown token: {name}")
            del records[name]
            self._write_index(records)
            self._write_credentials(records)

    def _read(self) -> dict[str, _TokenRecord]:
        path = self._dir / _INDEX_FILE
        if not path.exists():
            return {}
        raw: dict[str, object] = orjson.loads(path.read_bytes())
        return {name: _TokenRecord.model_validate(rec) for name, rec in raw.items()}

    def _write_index(self, records: dict[str, _TokenRecord]) -> None:
        payload = {name: rec.model_dump(mode="json") for name, rec in records.items()}
        write_secret(self._dir / _INDEX_FILE, orjson.dumps(payload).decode())

    def _write_credentials(self, records: dict[str, _TokenRecord]) -> None:
        # host вже валідований; user/token URL-encode'яться → жодних `@`/`/`/newline.
        lines = [
            f"https://{quote(rec.user, safe='')}:{quote(rec.token, safe='')}@{rec.host}"
            for rec in sorted(records.values(), key=lambda r: r.name)
        ]
        write_secret(self._dir / _CREDENTIALS_FILE, ("\n".join(lines) + "\n") if lines else "")


def _public(rec: _TokenRecord) -> ApiTokenMeta:
    return ApiTokenMeta(name=rec.name, host=rec.host, user=rec.user, created_at=rec.created_at)
