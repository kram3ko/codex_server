"""Shared vault primitives: strict boundary validation, atomic 0600 writes,
cross-process lock. Both `service` (ssh keys) and `tokens` (git creds) build
on these — values land in `~/.ssh/config` / `git-credentials`, so anything
unvalidated (newline/space/control) could inject extra directives or lines.
"""

import fcntl
import os
import string
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Charset whitelists — values land in `~/.ssh/config` / git-credentials, so the
# point is to forbid whitespace/control/`@`/`/` that could inject extra lines.
_NAME_START = frozenset(string.ascii_lowercase + string.digits)
_NAME_CHARS = _NAME_START | frozenset("-_")
_HOST_CHARS = frozenset(string.ascii_letters + string.digits + ".-_")
_USER_CHARS = _HOST_CHARS
_LOCK_FILE = ".lock"
_SECRET_MODE = 0o600


class SshVaultError(ValueError):
    """Boundary error — bad name/host/user, or unknown entry on delete.
    Re-adding an existing name is intentional update-in-place (rotate)."""


def validate_name(name: str) -> str:
    if not (1 <= len(name) <= 31 and name[0] in _NAME_START and set(name) <= _NAME_CHARS):
        raise SshVaultError("name must be lowercase letters/digits/-/_ (max 31 chars)")
    return name


def validate_host(host: str) -> str:
    if not (1 <= len(host) <= 253 and set(host) <= _HOST_CHARS):
        raise SshVaultError("host must be a hostname or IP (letters, digits, . - _)")
    return host


def resolve_user(user: str, default: str) -> str:
    user = user or default
    if not (1 <= len(user) <= 64 and set(user) <= _USER_CHARS):
        raise SshVaultError("user must be letters, digits, . - _ (max 64 chars)")
    return user


def write_secret(path: Path, body: str) -> None:
    """Atomic 0600 write — temp file opened with 0600, then `os.replace`.
    Avoids the brief 0644 window of `write_text()+chmod` and torn reads."""
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _SECRET_MODE)
    try:
        os.write(fd, body.encode())
    finally:
        os.close(fd)
    os.replace(tmp, path)


@contextmanager
def vault_lock(vault_dir: Path) -> Iterator[None]:
    """Cross-process exclusive lock around read→mutate→write — GUNICORN_WORKERS>1
    admins could otherwise clobber each other / leave torn json."""
    vault_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(vault_dir / _LOCK_FILE, os.O_WRONLY | os.O_CREAT, _SECRET_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
