from app.services.codex.events import TokenEvent, translate_notification
from app.services.codex.transport import Notification

_translate = translate_notification


def test_translate_skips_completed_agent_message_already_seen_in_deltas() -> None:
    note = Notification(
        method="item/completed",
        params={"item": {"type": "agentMessage", "text": "привет"}},
    )

    assert _translate(note, "привет") is None


def test_translate_uses_completed_agent_message_when_no_deltas_seen() -> None:
    note = Notification(
        method="item/completed",
        params={"item": {"type": "agentMessage", "text": "привет"}},
    )

    assert _translate(note, "") == TokenEvent(delta="привет")


def test_translate_only_adds_missing_suffix_from_completed_agent_message() -> None:
    note = Notification(
        method="item/completed",
        params={"item": {"type": "agentMessage", "text": "привет мир"}},
    )

    assert _translate(note, "привет") == TokenEvent(delta=" мир")
