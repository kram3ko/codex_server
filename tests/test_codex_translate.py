from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.events import (
    CodexItem,
    CodexNotif,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    translate_notification,
)
from app.services.codex.transport import Notification

_translate = translate_notification


def test_failed_turn_preserves_error() -> None:
    detail = "The model is not supported when using Codex with a ChatGPT account."
    note = Notification(
        method=CodexNotif.TURN_COMPLETED,
        params={"turn": {"status": "failed", "error": {"message": detail}}},
    )
    assert _translate(note, "") == ErrorEvent(code=CodexErrorCode.CODEX_ERROR, detail=detail)


def test_failed_turn_without_details_is_not_success() -> None:
    note = Notification(method=CodexNotif.TURN_COMPLETED, params={"turn": {"status": "failed"}})
    assert isinstance(_translate(note, "partial"), ErrorEvent)


def test_completed_turn_keeps_accumulated_text() -> None:
    note = Notification(
        method=CodexNotif.TURN_COMPLETED,
        params={"turn": {"status": "completed", "error": None}},
    )
    assert _translate(note, "answer") == DoneEvent(final_text="answer")


def _agent_msg_completed(text: str) -> Notification:
    return Notification(
        method=CodexNotif.ITEM_COMPLETED,
        params={"item": {"type": CodexItem.AGENT_MESSAGE, "text": text}},
        turn_id=None,
    )


def test_translate_skips_completed_agent_message_already_seen_in_deltas() -> None:
    assert _translate(_agent_msg_completed("привет"), "привет") is None


def test_translate_uses_completed_agent_message_when_no_deltas_seen() -> None:
    assert _translate(_agent_msg_completed("привет"), "") == TokenEvent(delta="привет")


def test_translate_only_adds_missing_suffix_from_completed_agent_message() -> None:
    assert _translate(_agent_msg_completed("привет мир"), "привет") == TokenEvent(delta=" мир")


def test_translate_drops_divergent_agent_message_no_doubling() -> None:
    # Колишня бомба: streamed text і final розійшлись (whitespace, token-rewrite)
    # → раніше дамп повного `text` поверх accumulated дублював відповідь у тій
    # самій бульбашці. Тепер None — авторитет на DoneEvent.final_text (в БД).
    assert _translate(_agent_msg_completed("Совсем другой ответ"), "Стрімилось щось одне") is None
