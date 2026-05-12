from app.services.codex.events import TokenEvent, translate_notification
from app.services.codex.transport import Notification

_translate = translate_notification


def _note(method: str, params: dict) -> Notification:
    return Notification(method=method, params=params, turn_id=params.get("turnId"))


def test_translate_skips_completed_agent_message_already_seen_in_deltas() -> None:
    note = _note(
        "item/completed",
        {"item": {"type": "agentMessage", "text": "привет"}},
    )

    assert _translate(note, "привет") is None


def test_translate_uses_completed_agent_message_when_no_deltas_seen() -> None:
    note = _note(
        "item/completed",
        {"item": {"type": "agentMessage", "text": "привет"}},
    )

    assert _translate(note, "") == TokenEvent(delta="привет")


def test_translate_only_adds_missing_suffix_from_completed_agent_message() -> None:
    note = _note(
        "item/completed",
        {"item": {"type": "agentMessage", "text": "привет мир"}},
    )

    assert _translate(note, "привет") == TokenEvent(delta=" мир")


def test_translate_drops_divergent_agent_message_no_doubling() -> None:
    # Колишня бомба: streamed text і final розійшлись (whitespace, token-rewrite)
    # → раніше дамп повного `text` поверх accumulated дублював відповідь у тій
    # самій бульбашці. Тепер None — авторитет на DoneEvent.final_text (в БД).
    note = _note(
        "item/completed",
        {"item": {"type": "agentMessage", "text": "Совсем другой ответ"}},
    )

    assert _translate(note, "Стрімилось щось одне") is None
