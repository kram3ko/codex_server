"""STT backend contract — all speech-to-text providers implement this Protocol."""

from pathlib import Path
from typing import Protocol


class AudioTranscriptionError(RuntimeError):
    pass


class STTBackend(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def transcribe(self, audio_path: Path) -> str: ...


class NullSTT:
    """Fallback when no STT credentials are configured. Returns empty silently."""

    enabled: bool = False

    async def transcribe(self, audio_path: Path) -> str:
        return ""
