import pytest

from app.tg.progress import TurnProgressReporter


class _StubChat:
    id = 1


class _StubBot:
    pass


class _OriginatorMessage:
    message_id = 42
    bot = _StubBot()
    chat = _StubChat()

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
async def test_stop_marks_status_as_completed_and_drops_controls() -> None:
    reporter = TurnProgressReporter(_OriginatorMessage())  # type: ignore[arg-type]
    status = _StatusMessage()
    reporter._status_message = status  # type: ignore[attr-defined]

    await reporter.stop()

    assert status.deleted is False
    assert status.edited_text is not None
    assert status.edited_text.startswith("✓ Завершено")
    assert "▓▓▓▓▓▓▓▓▓▓▓▓" in status.edited_text
    # Controls must be cleared after the turn — pressing them would be a no-op.
    assert status.reply_markup is None


@pytest.mark.asyncio
async def test_stop_marks_failed_outcome_when_set() -> None:
    reporter = TurnProgressReporter(_OriginatorMessage())  # type: ignore[arg-type]
    status = _StatusMessage()
    reporter._status_message = status  # type: ignore[attr-defined]
    reporter.mark_outcome("failed")

    await reporter.stop()

    assert status.edited_text is not None
    assert status.edited_text.startswith("✗ Помилка")
    assert status.reply_markup is None


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
async def test_note_partial_holds_back_short_buffer() -> None:
    reporter = TurnProgressReporter(_OriginatorMessage())  # type: ignore[arg-type]
    short = "теж замало щоб публікувати окремою бульбашкою"

    await reporter.note_partial(short)

    assert reporter.committed_text == ""


@pytest.mark.asyncio
async def test_note_partial_publishes_chunk_when_buffer_grows_past_threshold() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)  # type: ignore[arg-type]
    chunk = "Привіт. " * 40  # > _STREAM_MIN_CHARS

    await reporter.note_partial(chunk)

    assert len(originator.answers) == 1
    assert reporter.committed_text.startswith(chunk[:50])


@pytest.mark.asyncio
async def test_note_partial_throttles_consecutive_calls() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)  # type: ignore[arg-type]
    big = "Привіт. " * 40

    await reporter.note_partial(big)
    await reporter.note_partial(big + "ще трошки тексту, але одразу після першого виклику")

    assert len(originator.answers) == 1, "second call must hit throttle window"
