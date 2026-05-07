"""Process-wide TTS singleton — picked by configured credentials."""

import structlog

from app.services.tts.base import NullTTS, TTSBackend
from app.services.tts.google import GoogleTTS

log = structlog.get_logger(__name__)


def _build() -> TTSBackend:
    google = GoogleTTS()
    if google.enabled:
        log.info("tts_selected", backend="google")
        return google
    log.info("tts_selected", backend="none")
    return NullTTS()


tts_service: TTSBackend = _build()
