"""Heuristic language detection + Google voice mapping for TTS."""

_DEFAULT_LANGUAGE_CODE = "uk-UA"

# Chirp3-HD-Kore — Google's newest HD female voice, same timbre across all
# languages (звучить як одна людина перемикається мовами). Якщо мови розширюємо —
# теж брати Chirp3-HD-Kore у відповідній мові.
_VOICE_BY_LANGUAGE: dict[str, str] = {
    "uk-UA": "uk-UA-Chirp3-HD-Kore",
    "ru-RU": "ru-RU-Chirp3-HD-Kore",
    "en-US": "en-US-Chirp3-HD-Kore",
}

_UA_MARKERS = frozenset("їєіґЇЄІҐ")


def detect_language_code(text: str) -> str:
    """Return BCP-47 code for `text`. Falls back to uk-UA on empty/ambiguous input."""
    sample = text[:500]
    cyrillic = sum(1 for c in sample if "Ѐ" <= c <= "ӿ")
    latin = sum(1 for c in sample if c.isascii() and c.isalpha())

    if cyrillic > latin:
        return "uk-UA" if any(c in _UA_MARKERS for c in sample) else "ru-RU"
    if latin > 0:
        return "en-US"
    return _DEFAULT_LANGUAGE_CODE


def voice_for_language(language_code: str) -> str | None:
    """Return Wavenet voice name for `language_code`, or None to let Google pick."""
    return _VOICE_BY_LANGUAGE.get(language_code)
