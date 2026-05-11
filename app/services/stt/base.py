"""STT backend contract — all speech-to-text providers implement this Protocol."""

from typing import BinaryIO, Protocol


class AudioTranscriptionError(RuntimeError):
    pass


class STTBackend(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def transcribe(self, audio: BinaryIO, filename: str) -> str: ...


class NullSTT:
    """Fallback when no STT credentials are configured. Returns empty silently."""

    enabled: bool = False

    async def transcribe(self, audio: BinaryIO, filename: str) -> str:
        return ""
