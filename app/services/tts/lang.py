"""Language detection (langid restricted to uk/ru/en) + Google voice mapping."""

from langid.langid import LanguageIdentifier, model

# Chirp3-HD-Kore — Google's newest HD female voice, same timbre across all
# languages (звучить як одна людина перемикається мовами). Якщо мови розширюємо —
# теж брати Chirp3-HD-Kore у відповідній мові.
_VOICE_BY_LANGUAGE: dict[str, str] = {
    "uk-UA": "uk-UA-Chirp3-HD-Kore",
    "ru-RU": "ru-RU-Chirp3-HD-Kore",
    "en-US": "en-US-Chirp3-HD-Kore",
}

_LANGID_TO_BCP47: dict[str, str] = {
    "uk": "uk-UA",
    "ru": "ru-RU",
    "en": "en-US",
}

_DEFAULT_LANGUAGE_CODE = "uk-UA"
# Нижче цього confidence (langid `norm_probs=True` → [0..1]) — амбівалентно
# (типу "так" однаково uk/ru). Беремо default замість гадання.
_CONFIDENCE_THRESHOLD = 0.55

_identifier = LanguageIdentifier.from_modelstring(model, norm_probs=True)
_identifier.set_languages(list(_LANGID_TO_BCP47.keys()))


def detect_language_code(text: str) -> str:
    """Return BCP-47 code for `text`. Falls back to uk-UA on empty/ambiguous input."""
    sample = text.strip()[:500]
    if not sample:
        return _DEFAULT_LANGUAGE_CODE
    code, confidence = _identifier.classify(sample)
    if confidence < _CONFIDENCE_THRESHOLD:
        return _DEFAULT_LANGUAGE_CODE
    return _LANGID_TO_BCP47.get(code, _DEFAULT_LANGUAGE_CODE)


def voice_for_language(language_code: str) -> str | None:
    """Return Wavenet voice name for `language_code`, or None to let Google pick."""
    return _VOICE_BY_LANGUAGE.get(language_code)
