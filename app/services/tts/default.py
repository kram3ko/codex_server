"""Process-wide TTS singleton — currently always Null until a backend lands."""

import structlog

from app.services.tts.base import NullTTS, TTSBackend

log = structlog.get_logger(__name__)


def _build() -> TTSBackend:
    log.info("tts_selected", backend="none")
    return NullTTS()


tts_service: TTSBackend = _build()
