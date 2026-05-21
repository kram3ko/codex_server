import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from app.services.codex.client import (
    CodexClient,
    IdleDecision,
    StaleTurnStreamError,
    _TurnDiagnostics,
    decide_idle,
)
from app.services.codex.events import (
    CodexItem,
    CodexNotif,
    TokenEvent,
    iterate_with_idle_timeout,
)
from app.services.codex.transport import AppServerClient, Notification


class _FakeTransport(AppServerClient):
    """Test-only AppServerClient: skipa real WS, керується injected notes/calls.

    Real ctor викликається з dummy URL — `_FakeTransport` ніколи не з'єднується,
    тільки overrides public methods які CodexClient знає. Тип-сумісний з
    `AppServerClient`, тож CodexClient(..., transport=_FakeTransport(...)) — чистий DI.
    """

    def __init__(
        self,
        notes_factory: Callable[[], AsyncIterator[Notification]] | None = None,
    ) -> None:
        super().__init__(url="ws://test")
        self._notes_factory = notes_factory
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params or {}))
        return {}

    async def notifications(self) -> AsyncIterator[Notification]:
        if self._notes_factory is None:
            raise AssertionError("notes_factory not configured")
        async for note in self._notes_factory():
            yield note

    def diagnostic_snapshot(self) -> dict[str, Any]:
        return {}


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
async def test_current_turn_idle_ignores_stale_turn_notifications() -> None:
    async def stale_notes() -> AsyncIterator[Notification]:
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

    client = CodexClient(
        url="ws://unused",
        cwd="/tmp",
        approval_policy="never",
        sandbox="danger-full-access",
        transport=_FakeTransport(notes_factory=stale_notes),
    )
    client._current_turn_id = "new-turn"
    client._turn_diagnostics = _TurnDiagnostics(thread_id="thread", turn_id="new-turn")

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


@pytest.mark.asyncio
async def test_stale_turn_storm_resets_before_idle_timeout() -> None:
    from app.config import settings

    threshold = settings.CODEX_STALE_STORM_THRESHOLD

    async def stale_notes() -> AsyncIterator[Notification]:
        for _ in range(threshold):
            yield Notification(
                method=CodexNotif.ITEM_STARTED,
                params={"item": {"type": CodexItem.COMMAND_EXECUTION}},
                turn_id="old-turn",
            )

    client = CodexClient(
        url="ws://unused",
        cwd="/tmp",
        approval_policy="never",
        sandbox="danger-full-access",
        transport=_FakeTransport(notes_factory=stale_notes),
    )
    client._current_turn_id = "new-turn"
    client._turn_diagnostics = _TurnDiagnostics(thread_id="thread", turn_id="new-turn")

    with pytest.raises(StaleTurnStreamError) as raised:
        async for _ in client._current_turn_notifications(999, None):
            pass

    assert raised.value.diagnostics["raw_count"] == 0
    assert raised.value.diagnostics["stale_raw_count"] == threshold


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

    snapshot = diagnostics.snapshot(_FakeTransport())
    assert snapshot["active_items"] == 1
    assert snapshot["active_item_type"] == CodexItem.COMMAND_EXECUTION
    assert snapshot["active_tool"] == "shell"


@pytest.mark.asyncio
async def test_interrupt_sends_thread_id_and_turn_id() -> None:
    transport = _FakeTransport()
    client = CodexClient(
        url="ws://unused",
        cwd="/tmp",
        approval_policy="never",
        sandbox="danger-full-access",
        transport=transport,
    )

    interrupted = await client.interrupt(thread_id="thread-1", turn_id="turn-1")

    assert interrupted is True
    assert transport.calls == [("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"})]


def test_decide_idle_hard_cap_wins_over_active_items() -> None:
    decision = decide_idle(
        now=100.0,
        turn_started_at=0.0,
        has_active_items=True,
        last_idle_probe_at=99.0,  # would otherwise extend silently
        hard_cap_s=60.0,
        probe_interval_s=30.0,
    )

    assert decision is IdleDecision.HARD_CAP_EXCEEDED


def test_decide_idle_forces_probe_when_interval_elapsed() -> None:
    decision = decide_idle(
        now=100.0,
        turn_started_at=10.0,  # 90s into turn, well below hard cap
        has_active_items=True,
        last_idle_probe_at=60.0,  # 40s ago → exceeds 30s interval
        hard_cap_s=600.0,
        probe_interval_s=30.0,
    )

    assert decision is IdleDecision.NEEDS_PROBE


def test_decide_idle_skips_probe_when_recent() -> None:
    decision = decide_idle(
        now=100.0,
        turn_started_at=10.0,
        has_active_items=True,
        last_idle_probe_at=95.0,  # 5s ago, fresh
        hard_cap_s=600.0,
        probe_interval_s=30.0,
    )

    assert decision is IdleDecision.EXTEND_SILENTLY


def test_decide_idle_runs_probe_when_no_active_items() -> None:
    decision = decide_idle(
        now=100.0,
        turn_started_at=10.0,
        has_active_items=False,
        last_idle_probe_at=99.5,  # recent, but no active items → probe anyway
        hard_cap_s=600.0,
        probe_interval_s=30.0,
    )

    assert decision is IdleDecision.NEEDS_PROBE


def test_decide_idle_runs_probe_on_first_idle_hit() -> None:
    decision = decide_idle(
        now=100.0,
        turn_started_at=10.0,
        has_active_items=True,
        last_idle_probe_at=None,  # never probed yet
        hard_cap_s=600.0,
        probe_interval_s=30.0,
    )

    assert decision is IdleDecision.NEEDS_PROBE


def test_decide_idle_skips_hard_cap_when_turn_not_started() -> None:
    decision = decide_idle(
        now=100.0,
        turn_started_at=None,
        has_active_items=False,
        last_idle_probe_at=None,
        hard_cap_s=0.001,
        probe_interval_s=30.0,
    )

    assert decision is IdleDecision.NEEDS_PROBE
