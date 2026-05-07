import pytest

from app.tg.progress import TurnProgressReporter


class _OriginatorMessage:
    message_id = 42

    def __init__(self) -> None:
        self.answers: list[tuple[str, object | None]] = []

    async def answer(self, text: str, *, reply_markup=None) -> _StatusMessage:
        self.answers.append((text, reply_markup))
        return _StatusMessage()


class _StatusMessage:
    def __init__(self) -> None:
        self.deleted = False
        self.edited_text: str | None = None
        self.reply_markup = object()

    async def delete(self) -> None:
        self.deleted = True

    async def edit_text(self, text: str, *, reply_markup=None) -> None:
        self.edited_text = text
        self.reply_markup = reply_markup


@pytest.mark.asyncio
async def test_stop_keeps_status_message_with_controls_as_completed_marker() -> None:
    reporter = TurnProgressReporter(_OriginatorMessage())  # type: ignore[arg-type]
    status = _StatusMessage()
    reporter._status_message = status  # type: ignore[attr-defined]

    await reporter.stop()

    assert status.deleted is False
    assert status.edited_text is not None
    assert status.edited_text.startswith("✓ Завершено")
    assert "▓▓▓▓▓▓▓▓▓▓▓▓" in status.edited_text
    assert status.reply_markup is not None


@pytest.mark.asyncio
async def test_stop_skips_duplicate_completed_marker_edit() -> None:
    reporter = TurnProgressReporter(_OriginatorMessage())  # type: ignore[arg-type]
    status = _StatusMessage()
    reporter._status_message = status  # type: ignore[attr-defined]
    reporter._last_status_text = reporter._compose_status_text(done=True)  # type: ignore[attr-defined]

    await reporter.stop()

    assert status.edited_text is None


def test_status_message_contains_progress_bar() -> None:
    reporter = TurnProgressReporter(_OriginatorMessage())  # type: ignore[arg-type]

    text = reporter._compose_status_text()  # type: ignore[attr-defined]

    assert text.startswith("⏳ Thinking…")
    assert any(line.startswith("▓") or line.startswith("░") for line in text.splitlines())


@pytest.mark.asyncio
async def test_refresh_status_creates_message_without_tools() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)  # type: ignore[arg-type]

    await reporter._refresh_status()  # type: ignore[attr-defined]

    assert len(originator.answers) == 1
    assert originator.answers[0][0].startswith("⏳ Thinking…")
    assert originator.answers[0][1] is not None


@pytest.mark.asyncio
async def test_partial_text_is_not_rendered_as_draft() -> None:
    reporter = TurnProgressReporter(
        _OriginatorMessage(),  # type: ignore[arg-type]
        draft_enabled=True,
    )

    await reporter.note_partial("streamed assistant text")

    assert reporter._compose_draft_text() == ""  # type: ignore[attr-defined]
    assert reporter._last_draft_text == ""  # type: ignore[attr-defined]
