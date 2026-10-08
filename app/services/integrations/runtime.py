from pathlib import Path
from urllib.parse import quote

import orjson

from app.models import Integration
from app.services.integrations.crypto import SecretCipher
from app.services.integrations.schemas import IntegrationKind
from app.services.ssh_vault._common import vault_lock, write_secret


def materialize(rows: list[Integration], cipher: SecretCipher, directory: str) -> None:
    root = Path(directory)
    with vault_lock(root):
        active = [row for row in rows if row.enabled and row.kind != IntegrationKind.TELEGRAM]
        try:
            decoded = [(row, cipher.unseal(row.secret)) for row in active]
        except ValueError:
            write_secret(root / "config", "")
            write_secret(root / "git-credentials", "")
            write_secret(root / "cli-tokens.json", "[]")
            for path in root.glob("managed_*"):
                path.unlink()
            raise
        config: list[str] = []
        credentials: list[str] = []
        tokens: list[dict[str, str]] = []
        keep: set[str] = set()
        for row, secret in decoded:
            if row.kind == IntegrationKind.SSH:
                filename = f"managed_{row.id}"
                keep.add(filename)
                write_secret(root / filename, secret + "\n")
                config.append(
                    f"Host {row.name}\n    HostName {row.host}\n    User {row.username}\n"
                    f"    IdentityFile ~/.ssh/{filename}\n    IdentitiesOnly yes\n"
                    "    StrictHostKeyChecking accept-new\n"
                )
            else:
                user = (
                    row.username or "oauth2"
                    if row.kind == IntegrationKind.GITLAB
                    else row.username or "x-access-token"
                )
                credentials.append(
                    f"https://{quote(user, safe='')}:{quote(secret, safe='')}@{row.host}"
                )
                tokens.append({"kind": row.kind, "host": row.host, "token": secret})
        write_secret(root / "config", "\n".join(config))
        write_secret(root / "git-credentials", "\n".join(credentials) + "\n")
        write_secret(root / "cli-tokens.json", orjson.dumps(tokens).decode())
        for path in root.glob("managed_*"):
            if path.name not in keep:
                path.unlink()
