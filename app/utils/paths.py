"""Trusted-root resolver for tool-supplied file paths."""

from pathlib import Path
from urllib.parse import urlparse

from app.config import settings

_WORKSPACE_ROOT = Path(settings.CODEX_CWD).resolve()
# image_generation пише поза workspace — окремо у whitelist.
_TRUSTED_OUTPUT_ROOTS: tuple[Path, ...] = (
    _WORKSPACE_ROOT,
    Path("/home/codex/.codex/generated_images").resolve(),
)


def resolve_trusted_local_path(source: str) -> Path | None:
    """Return existing file under a trusted root; None for URLs or untrusted paths."""
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return None
    path = Path(source).expanduser()
    path = (_WORKSPACE_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not _is_inside_trusted_root(path) or not path.is_file():
        return None
    return path


def _is_inside_trusted_root(path: Path) -> bool:
    return any(path.is_relative_to(root) for root in _TRUSTED_OUTPUT_ROOTS)
