"""TTS backend contract — placeholder package, mirrors `stt/`."""

from pathlib import Path
from typing import Protocol


class SpeechSynthesisError(RuntimeError):
    pass


class TTSBackend(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def synthesize(self, text: str, out_path: Path) -> Path:
        """Render `text` to `out_path` (audio file). Returns the same path."""
        ...


class NullTTS:
    """Fallback when no TTS credentials are configured. Not enabled."""

    enabled: bool = False

    async def synthesize(self, text: str, out_path: Path) -> Path:
        raise SpeechSynthesisError("no TTS backend configured")
