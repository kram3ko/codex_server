"""Speechmatics batch v2 STT backend."""

import contextlib
import tempfile
from pathlib import Path
from typing import BinaryIO

import structlog
from speechmatics.batch import (
    AsyncClient,
    JobError,
    OperatingPoint,
    Transcript,
    TranscriptionConfig,
)

from app.config import settings
from app.services.stt.base import AudioTranscriptionError

log = structlog.get_logger(__name__)

# Upper-bound на один turn. TG voice ноти зазвичай 30-90s; 3хв з запасом.
_TIMEOUT_S = 180.0
# Polling cadence на /jobs/{id} — компроміс між швидким першим читанням і
# не-перевантаженням Speechmatics rate limit.
_POLL_S = 1.5
# Маркери: Speechmatics rejected job без мовлення → нам це не помилка,
# просто "транскрипту нема". Підрядковий match по тексту exception, бо
# SDK не дає окремого exception type для цього кейсу.
_NO_SPEECH_MARKERS = ("no speech", "language identification")


class SpeechmaticsSTT:
    def __init__(self) -> None:
        self._api_key = settings.SPEECHMATICS_API_KEY.strip()
        self._language = settings.SPEECHMATICS_LANGUAGE.strip() or "auto"
        self._operating_point = settings.SPEECHMATICS_OPERATING_POINT.strip() or "enhanced"

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def transcribe(self, audio: BinaryIO, filename: str) -> str:
        """Returns transcript text; empty on silence/no-speech (not an error).

        Speechmatics SDK accepts only a `str` path, so we materialize the
        BytesIO into a NamedTemporaryFile and clean up right after. Public
        interface stays streaming-friendly for callers."""
        if not self.enabled:
            return ""

        audio.seek(0)
        data = audio.read()
        with tempfile.NamedTemporaryFile(
            suffix=Path(filename).suffix or ".bin", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
        tmp_path.write_bytes(data)

        config = TranscriptionConfig(
            language=self._language,
            operating_point=OperatingPoint(self._operating_point),
        )
        try:
            async with AsyncClient(api_key=self._api_key) as client:
                try:
                    result = await client.transcribe(
                        audio_file=str(tmp_path),
                        transcription_config=config,
                        polling_interval=_POLL_S,
                        timeout=_TIMEOUT_S,
                    )
                except JobError as exc:
                    if _is_no_speech(str(exc)):
                        log.info("speechmatics_no_speech", reason=str(exc)[:120])
                        return ""
                    raise AudioTranscriptionError(f"Speechmatics job failed: {exc}") from exc
                except Exception as exc:  # noqa: BLE001
                    raise AudioTranscriptionError(
                        f"Speechmatics transcription failed: {exc}"
                    ) from exc
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()

        if not isinstance(result, Transcript):
            raise AudioTranscriptionError(f"Unexpected Speechmatics result type: {type(result)}")
        text = (result.transcript_text or "").strip()
        log.info(
            "speechmatics_transcribed",
            length=len(text),
            language=self._language,
            empty=not text,
        )
        return text


def _is_no_speech(msg: str) -> bool:
    low = msg.lower()
    return any(marker in low for marker in _NO_SPEECH_MARKERS)
