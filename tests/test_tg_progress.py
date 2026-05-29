import pytest

from app.tg import progress
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
    is_topic_message = False
    message_thread_id = None

    def __init__(self) -> None:
        self.answers: list[tuple[str, object | None]] = []
        self._next_status: _StatusMessage | None = None

    def stub_next_status(self, status: _StatusMessage) -> None:
        self._next_status = status

    async def answer(
        self, text: str, *, reply_markup=None, message_thread_id=None
    ) -> _StatusMessage:
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
async def test_note_partial_ignores_empty_buffer() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)

    await reporter.note_partial("   ")

    # Порожній/пробільний буфер не створює стрім-бульбашку.
    assert originator.answers == []


@pytest.mark.asyncio
async def test_note_partial_mirrors_buffer_into_single_message() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)
    chunk = "Привіт. " * 40

    await reporter.note_partial(chunk)

    # Один редагований меседж, не серія бульбашок.
    assert len(originator.answers) == 1
    assert "Привіт" in originator.answers[0][0]


@pytest.mark.asyncio
async def test_note_partial_throttles_consecutive_calls() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)
    big = "Привіт. " * 40

    await reporter.note_partial(big)
    await reporter.note_partial(big + "ще трошки тексту, але одразу після першого виклику")

    assert len(originator.answers) == 1, "second call must hit throttle window"


@pytest.mark.asyncio
async def test_note_partial_edits_existing_message_after_throttle(monkeypatch) -> None:
    monkeypatch.setattr(progress, "_STREAM_THROTTLE_S", 0.0)
    originator = _OriginatorMessage()
    status = _StatusMessage()
    originator.stub_next_status(status)
    reporter = TurnProgressReporter(originator)

    await reporter.note_partial("Привіт. " * 40)
    await reporter.note_partial("Привіт. " * 40 + "продовження після троттлу")

    # Бульбашка створюється раз, далі лише edit_text того ж меседжа.
    assert len(originator.answers) == 1
    assert status.edited_text is not None
    assert "продовження" in status.edited_text


@pytest.mark.asyncio
async def test_finalize_stream_publishes_and_returns_visible() -> None:
    originator = _OriginatorMessage()
    reporter = TurnProgressReporter(originator)

    returned = await reporter.finalize_stream("Фінальна відповідь")

    assert returned == "Фінальна відповідь"
    assert len(originator.answers) == 1
    assert "Фінальна" in originator.answers[0][0]
