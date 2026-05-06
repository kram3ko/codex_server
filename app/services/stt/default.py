"""Process-wide STT singleton — picked by configured credentials."""

import structlog

from app.services.stt.base import NullSTT, STTBackend
from app.services.stt.speechmatics import SpeechmaticsSTT

log = structlog.get_logger(__name__)


def _build() -> STTBackend:
    speechmatics = SpeechmaticsSTT()
    if speechmatics.enabled:
        log.info("stt_selected", backend="speechmatics")
        return speechmatics
    log.info("stt_selected", backend="none")
    return NullSTT()


stt_service: STTBackend = _build()
