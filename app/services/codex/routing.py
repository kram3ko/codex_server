"""Per-turn маршрутизатор Codex-нотифікацій.

`AppServerClient` доставляє ВСІ ноти одним handler-callback'ом. На рівні
одної WS-сесії можуть пройти багато турнів послідовно — і Codex sidecar
може прислати `item/completed` для попереднього турну вже після того, як
`turn/completed` для нього розіслали (race на боці sidecar'а). Якщо такі
ноти потрапляють у наступний `run_turn`, він бачить «голос з минулого»
і друкує попередню відповідь у нову бульбашку.

TurnRouter це фіксить архітектурно: ноти буферяться по `turn_id`, кожен
`run_turn` отримує subscription **лише на свій** turn_id. Чужі ноти
фізично не можуть дістатися до нього.

Інстанс-скоуп: один router на одну WS-сесію (== одного `CodexClient`).
Жодних модульних singleton'ів.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Final

import structlog

from app.services.codex.transport import AppServerClient, Notification

log = structlog.get_logger(__name__)

# Розмір буфера на один turn. Codex стрімить десятки delta + кілька item-нот,
# 256 з запасом. QueueFull маркує сигнал що caller не встигає.
_BUFFER_MAX: Final[int] = 256


class TurnRouter:
    """Фановтить notifications з transport'у у per-turn буфери.

    Сабскрайбер (`run_turn`) бачить тільки ноти свого turn_id; ноти інших
    турнів (leftover з попередніх або orphan з sidecar race) дропаються при
    `subscribe_turn` нового turn'а.
    """

    def __init__(self, transport: AppServerClient) -> None:
        self._transport = transport
        self._buffers: dict[str, asyncio.Queue[Notification | None]] = {}
        transport.set_notification_handler(self._on_notification)
        transport.set_close_handler(self._on_close)

    @contextlib.asynccontextmanager
    async def subscribe_turn(self, turn_id: str) -> AsyncIterator[AsyncIterator[Notification]]:
        """Підписатись на ноти конкретного турну.

        Перед підпискою evict'имо буфери всіх ІНШИХ турнів — на рівні одного
        thread'а в Codex sidecar в один момент часу активний рівно один turn,
        тож будь-які leftover-буфери з минулого мертві. Це і є архітектурне
        вирішення «прилітання попереднього повідомлення».

        Yields: async iterator, що завершується коли transport закривається
        або context exit'ить. Caller відповідає за завершення на DoneEvent.
        """
        self._evict_stale(keep=turn_id)
        queue = self._buffers.get(turn_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=_BUFFER_MAX)
            self._buffers[turn_id] = queue
        try:
            yield self._consume(queue)
        finally:
            self._buffers.pop(turn_id, None)

    def _on_notification(self, note: Notification) -> None:
        if note.turn_id is None:
            # Session-level (initialized тощо) — translator однаково повертає None.
            log.debug("codex_session_note_dropped", method=note.method)
            return
        queue = self._buffers.get(note.turn_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=_BUFFER_MAX)
            self._buffers[note.turn_id] = queue
        try:
            queue.put_nowait(note)
        except asyncio.QueueFull:
            log.warning(
                "codex_turn_buffer_full",
                turn_id=note.turn_id,
                method=note.method,
                size=queue.qsize(),
            )

    def _on_close(self) -> None:
        for queue in self._buffers.values():
            self._signal_end(queue)

    def _evict_stale(self, *, keep: str) -> None:
        stale = [tid for tid in self._buffers if tid != keep]
        for tid in stale:
            queue = self._buffers.pop(tid)
            if queue.qsize():
                log.info("codex_evicted_stale_turn", turn_id=tid, dropped=queue.qsize())
            self._signal_end(queue)

    @staticmethod
    def _signal_end(queue: asyncio.Queue[Notification | None]) -> None:
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    async def _consume(
        self,
        queue: asyncio.Queue[Notification | None],
    ) -> AsyncIterator[Notification]:
        while True:
            note = await queue.get()
            if note is None:
                return
            yield note
