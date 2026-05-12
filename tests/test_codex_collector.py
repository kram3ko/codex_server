"""Unit-тести для StreamCollector — джерело правди для накопичення стану турну."""

from app.services.codex.collector import StreamCollector
from app.services.codex.events import (
    Attachment,
    AttachmentKind,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)


def test_token_appends_to_buffer() -> None:
    c = StreamCollector()
    c.absorb(TokenEvent(delta="hello "))
    c.absorb(TokenEvent(delta="world"))

    assert c.buffer == "hello world"
    assert c.done_seen is False


def test_tool_call_recorded() -> None:
    c = StreamCollector()
    c.absorb(ToolCallEvent(name="shell", args={"command": "ls"}))

    assert c.tool_calls == [{"name": "shell", "args": {"command": "ls"}}]


def test_tool_result_attachments_extended() -> None:
    c = StreamCollector()
    a1 = Attachment(kind=AttachmentKind.IMAGE, source="/x/1.png")
    a2 = Attachment(kind=AttachmentKind.IMAGE, source="/x/2.png")
    c.absorb(ToolResultEvent(name="image_gen", attachments=(a1, a2)))

    assert c.attachments == [a1, a2]


def test_done_sets_final_text_and_flag() -> None:
    c = StreamCollector()
    c.absorb(TokenEvent(delta="готово"))
    c.absorb(DoneEvent(final_text="готово"))

    assert c.done_seen is True
    assert c.final_text == "готово"
    assert c.buffer == "готово"


def test_error_event_does_not_mutate_state() -> None:
    c = StreamCollector()
    c.absorb(TokenEvent(delta="part"))
    c.absorb(ErrorEvent(code="codex_error", detail="boom"))

    assert c.buffer == "part"
    assert c.done_seen is False
    assert c.final_text == ""
