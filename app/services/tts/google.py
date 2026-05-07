"""Google Cloud Text-to-Speech backend (REST v1, API key auth)."""

import base64
from pathlib import Path

import httpx
import structlog

from app.config import settings
from app.services.tts.base import SpeechSynthesisError
from app.services.tts.lang import detect_language_code, voice_for_language

log = structlog.get_logger(__name__)

_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
_TIMEOUT_S = 30.0


class GoogleTTS:
    def __init__(self) -> None:
        self._api_key = settings.GOOGLE_TTS_API_KEY.strip()
        self._audio_encoding = settings.GOOGLE_TTS_AUDIO_ENCODING.strip() or "MP3"

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def synthesize(
        self,
        text: str,
        out_path: Path,
        language_code: str | None = None,
        audio_encoding: str | None = None,
    ) -> Path:
        if not self.enabled:
            raise SpeechSynthesisError("GOOGLE_TTS_API_KEY is not set")
        if not text.strip():
            raise SpeechSynthesisError("text is empty")

        language = language_code or detect_language_code(text)
        encoding = (audio_encoding or self._audio_encoding).strip() or "MP3"
        voice: dict[str, str] = {"languageCode": language}
        voice_name = voice_for_language(language)
        if voice_name:
            voice["name"] = voice_name

        payload = {
            "input": {"text": text},
            "voice": voice,
            "audioConfig": {"audioEncoding": encoding},
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(
                    _ENDPOINT,
                    params={"key": self._api_key},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise SpeechSynthesisError(f"Google TTS request failed: {exc}") from exc

        if resp.status_code != 200:
            raise SpeechSynthesisError(
                f"Google TTS HTTP {resp.status_code}: {_extract_error(resp)}"
            )

        audio_b64 = resp.json().get("audioContent")
        if not audio_b64:
            raise SpeechSynthesisError("Google TTS returned no audioContent")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(audio_b64))
        log.info(
            "google_tts_synthesized",
            language=language,
            voice=voice_name or "default",
            encoding=encoding,
            text_length=len(text),
            bytes=out_path.stat().st_size,
            detected=language_code is None,
        )
        return out_path


def _extract_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error") or {}
        return str(err.get("message") or body)[:300]
    except ValueError:
        return resp.text[:300]
