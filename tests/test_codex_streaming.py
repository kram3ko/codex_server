import asyncio

import pytest

from app.services.codex.events import (
    CodexItem,
    CodexNotif,
    TokenEvent,
    iterate_with_idle_timeout,
)
from app.services.codex.client import CodexClient, _TurnDiagnostics
from app.services.codex.transport import Notification


async def _events():
    yield TokenEvent(delta="ok")


async def _silent():
    await asyncio.sleep(10)
    yield TokenEvent(delta="never")


@pytest.mark.asyncio
async def test_iterate_with_idle_timeout_yields_events() -> None:
    events = [event async for event in iterate_with_idle_timeout(_events(), 1.0)]

    assert events == [TokenEvent(delta="ok")]


@pytest.mark.asyncio
async def test_iterate_with_idle_timeout_calls_on_idle() -> None:
    called = False

    async def on_idle() -> None:
        nonlocal called
        called = True

    with pytest.raises(TimeoutError):
        async for _ in iterate_with_idle_timeout(_silent(), 0.001, on_idle=on_idle):
            pass

    assert called is True


@pytest.mark.asyncio
async def test_idle_timeout_resets_on_hidden_notifications() -> None:
    # Regression: codex шле `item/started{reasoning}` під час довгого
    # chain-of-thought, translator повертає None → ChatEvent stream тихий.
    # Watchdog має обгортати СИРИЙ Notification stream (всередині run_turn),
    # тож reasoning-ноти ресетують timer і turn не фалшиво-помирає на idle.
    async def reasoning_then_done():
        for _ in range(5):
            await asyncio.sleep(0.02)
            yield Notification(
                method=CodexNotif.ITEM_STARTED,
                params={"item": {"type": CodexItem.REASONING}},
                turn_id="t1",
            )
        yield Notification(
            method=CodexNotif.TURN_COMPLETED, params={"finalText": "done"}, turn_id="t1"
        )

    items = [n async for n in iterate_with_idle_timeout(reasoning_then_done(), 0.1)]

    assert len(items) == 6
    assert items[-1].method == CodexNotif.TURN_COMPLETED


@pytest.mark.asyncio
async def test_current_turn_idle_ignores_stale_turn_notifications(monkeypatch) -> None:
    client = CodexClient(
        url="ws://unused",
        cwd="/tmp",
        approval_policy="never",
        sandbox="danger-full-access",
    )
    client._current_turn_id = "new-turn"
    client._turn_diagnostics = _TurnDiagnostics(thread_id="thread", turn_id="new-turn")

    async def stale_notes():
        # Детермінований flow: 5 stale notes без затримки, потім вічне чекання
        # триггерить idle timeout. Без `asyncio.sleep` яке flaky на Windows через
        # 15ms timer granularity.
        for _ in range(5):
            yield Notification(
                method=CodexNotif.ITEM_STARTED,
                params={"item": {"type": CodexItem.COMMAND_EXECUTION}},
                turn_id="old-turn",
            )
        await asyncio.Event().wait()
        yield  # unreachable

    monkeypatch.setattr(client._transport, "notifications", stale_notes)

    called = False

    async def on_idle() -> None:
        nonlocal called
        called = True

    with pytest.raises(TimeoutError):
        async for _ in client._current_turn_notifications(0.1, on_idle):
            pass

    diagnostics = client.turn_diagnostics()
    assert called is True
    assert diagnostics["raw_count"] == 0
    assert diagnostics["stale_raw_count"] == 5


def test_turn_diagnostics_keeps_shell_active_under_reasoning() -> None:
    diagnostics = _TurnDiagnostics(thread_id="thread", turn_id="turn")
    diagnostics.absorb_raw(
        Notification(
            method=CodexNotif.ITEM_STARTED,
            params={"item": {"id": "cmd", "type": CodexItem.COMMAND_EXECUTION}},
            turn_id="turn",
        )
    )
    diagnostics.absorb_raw(
        Notification(
            method=CodexNotif.ITEM_STARTED,
            params={"item": {"id": "reason", "type": CodexItem.REASONING}},
            turn_id="turn",
        )
    )
    diagnostics.absorb_raw(
        Notification(
            method=CodexNotif.ITEM_COMPLETED,
            params={"item": {"id": "reason", "type": CodexItem.REASONING}},
            turn_id="turn",
        )
    )

    snapshot = diagnostics.snapshot(_DummyTransport())
    assert snapshot["active_items"] == 1
    assert snapshot["active_item_type"] == CodexItem.COMMAND_EXECUTION
    assert snapshot["active_tool"] == "shell"


class _DummyTransport:
    def diagnostic_snapshot(self) -> dict[str, object]:
        return {}
