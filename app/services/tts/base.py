"""TTS backend contract — all text-to-speech providers implement this Protocol."""

from pathlib import Path
from typing import Protocol


class SpeechSynthesisError(RuntimeError):
    pass


class TTSBackend(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def synthesize(
        self,
        text: str,
        out_path: Path,
        language_code: str | None = None,
        audio_encoding: str | None = None,
    ) -> Path:
        """Render `text` to `out_path` (audio file). Returns the same path.

        `language_code` — BCP-47; if None, backend autodetects from text.
        `audio_encoding` — provider-specific (e.g. MP3, OGG_OPUS); None → settings default.
        """
        ...


class NullTTS:
    """Fallback when no TTS credentials are configured. Not enabled."""

    enabled: bool = False

    async def synthesize(
        self,
        text: str,
        out_path: Path,
        language_code: str | None = None,
        audio_encoding: str | None = None,
    ) -> Path:
        raise SpeechSynthesisError("no TTS backend configured")
