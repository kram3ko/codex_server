import pytest

from app.tg.progress import TurnProgressReporter


class _StubChat:
    id = 1


class _StubBot:
    pass


class _StatusMessage:
    def __init__(self) -> None:
        self.deleted = False
        self.edited_text: str | None = None
        self.reply_markup: object | None = object()

    async def delete(self) -> None:
        self.deleted = True

    async def edit_text(self, text: str, *, reply_markup=None) -> None:
        self.edited_text = text
        self.reply_markup = reply_markup


class _OriginatorMessage:
    message_id = 42
    bot = _StubBot()
    chat = _StubChat()

    def __init__(self) -> None:
        self.answers: list[tuple[str, object | None]] = []
        self._next_status: _StatusMessage | None = None

    def stub_next_status(self, status: _StatusMessage) -> None:
        self._next_status = status

    async def answer(self, text: str, *, reply_markup=None) -> _StatusMessage:
        self.answers.append((text, reply_markup))
        status = self._next_status or _StatusMessage()
        self._next_status = None
        return status


async def _setup_reporter_with_status() -> tuple[
    TurnProgressReporter, _OriginatorMessage, _StatusMessage
]:
    """Прокручує public-flow: refresh_status() створює status message через
    `originator.answer()`, що повертає підкинутий `_StatusMessage`. Без
    poking приватного state."""
    originator = _OriginatorMessage()
    status = _StatusMessage()
    originator.stub_next_status(status)
    reporter = TurnProgressReporter(originator)
    await reporter.refresh_status()
    return reporter, originator, status


@pytest.mark.asyncio
async def test_stop_deletes_status_message() -> None:
    reporter, _originator, status = await _setup_reporter_with_status()

    await reporter.stop()

    # Прибираємо статус повністю — фінального "Завершено" немає (юзеру важлива
    # сама відповідь, а не post-факт індикатор).
    assert status.deleted is True


@pytest.mark.asyncio
async def test_stop_deletes_status_regardless_of_outcome() -> None:
    reporter, _originator, status = await _setup_reporter_with_status()

    await reporter.stop()

    assert status.deleted is True


def test_status_text_thinking_when_no_active_tool() -> None:
    reporter = TurnProgressReporter(_OriginatorMessage())

    assert reporter.compose_status_text() == "⏳ Thinking…"


@pytest.mark.asyncio
async def test_status_text_shows_only_running_tool() -> None:
    reporter = TurnProgressReporter(_OriginatorMessage())

    await reporter.note_tool("read_file")
    assert reporter.compose_status_text() == "🔧 read_file"

    await reporter.mark_tool_done("read_file")
    # Завершені тулзи не висять у статусі — лишається базовий "Thinking".
    assert reporter.compose_status_text() == "⏳ Thinking…"

    await reporter.note_tool("write_file")
    assert reporter.compose_status_text() == "🔧 write_file"


@pytest.mark.asyncio
async def test_refresh_status_creates_message_without_tools() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)

    await reporter.refresh_status()

    assert len(originator.answers) == 1
    assert originator.answers[0][0].startswith("⏳ Thinking…")
    assert originator.answers[0][1] is not None


@pytest.mark.asyncio
async def test_note_partial_holds_back_short_buffer() -> None:
    reporter = TurnProgressReporter(_OriginatorMessage())
    short = "теж замало щоб публікувати окремою бульбашкою"

    await reporter.note_partial(short)

    assert reporter.committed_text == ""


@pytest.mark.asyncio
async def test_note_partial_publishes_chunk_when_buffer_grows_past_threshold() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)
    chunk = "Привіт. " * 40  # > _STREAM_MIN_CHARS

    await reporter.note_partial(chunk)

    assert len(originator.answers) == 1
    assert reporter.committed_text.startswith(chunk[:50])


@pytest.mark.asyncio
async def test_note_partial_throttles_consecutive_calls() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)
    big = "Привіт. " * 40

    await reporter.note_partial(big)
    await reporter.note_partial(big + "ще трошки тексту, але одразу після першого виклику")

    assert len(originator.answers) == 1, "second call must hit throttle window"
