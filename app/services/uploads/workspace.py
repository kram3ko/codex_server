"""Materialize non-image uploads under workspace so Codex can read them.

Codex CLI 0.132 на wire-форматі приймає у `turn/start` лише
`text | image | localImage | skill | mention`. PDF/DOCX/CSV/code/архіви
доставляються через workspace: пишемо у `<root>/uploads/chat-<id>/`,
у user-тексті згадуємо relative path, Codex читає через свій shell tool.

Папка `uploads/` уже у `.gitignore` репо.
"""

import asyncio
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path

_SAFE_EXTRA_CHARS = frozenset("._-")


class WorkspaceUploads:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._uploads_root = root / "uploads"

    async def materialize_for_chat(
        self,
        data: bytes | BytesIO,
        *,
        chat_id: int,
        upload_id: int,
        filename: str,
    ) -> str:
        """Write bytes under ``uploads/chat-<chat_id>/<upload_id>-<safe>``.

        Returns workspace-relative POSIX path для згадки у user-тексті.
        Disk I/O — у `to_thread`, щоб не блокувати event loop на великих файлах.
        """
        payload = data.getvalue() if isinstance(data, BytesIO) else data
        relative = (
            Path("uploads") / f"chat-{chat_id}" / f"{upload_id}-{self._safe_filename(filename)}"
        )
        target = self._root / relative
        await asyncio.to_thread(self._write, target, payload)
        return relative.as_posix()

    @staticmethod
    def format_mentions(paths: Iterable[str]) -> str:
        """Build a user-text snippet that points Codex до файлів у `cwd`.

        Codex читає через shell tool — рядок повинен бути однозначним, щоб модель
        розпізнала що це paths під workspace root. Пустий iterable → "".
        """
        items = list(paths)
        if not items:
            return ""
        bullets = "\n".join(f"- `{path}`" for path in items)
        return f"Attached files (open from cwd):\n{bullets}"

    @staticmethod
    def _safe_filename(filename: str) -> str:
        base = Path(filename).name
        cleaned = "".join(c if c.isalnum() or c in _SAFE_EXTRA_CHARS else "_" for c in base)
        return cleaned.strip("._-") or "file"

    @staticmethod
    def _write(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
