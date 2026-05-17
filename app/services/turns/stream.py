"""Per-turn Redis stream. Stream key per `turn.id` → cross-turn contamination
неможлива. Empty XREAD: продовжуємо чекати поки `turn.status` non-terminal у
БД, інакше виходимо — caller потім читає БД-status і yield-ить synthetic
terminal."""

from collections.abc import AsyncIterator

from app.config import settings
from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import chat_pb2
from app.models import TURN_TERMINAL_STATUSES
from app.services.cache.default import binary_cache


class TurnStream:
    def _key(self, turn_id: int) -> str:
        return f"turn:{turn_id}:events"

    async def publish(self, turn_id: int, event: chat_pb2.ChatEvent) -> str:
        raw_id = await binary_cache.xadd(
            self._key(turn_id),
            {"pb": event.SerializeToString()},
            maxlen=settings.TURN_STREAM_MAX_EVENTS,
            approximate=True,
        )
        await binary_cache.expire(self._key(turn_id), settings.TURN_STREAM_TTL_S)
        return raw_id.decode("ascii")

    async def tail(
        self,
        turn_id: int,
        after_event_id: str,
    ) -> AsyncIterator[chat_pb2.ChatEvent]:
        from app.services.turns.default import turn_service

        cursor = after_event_id or "0"
        while True:
            response = await binary_cache.xread(
                {self._key(turn_id): cursor},
                block=settings.TURN_STREAM_TAIL_BLOCK_MS,
                count=64,
            )
            if not response:
                # Empty XREAD — race між worker publish і handler read. Виходимо
                # тільки якщо turn вже terminal у БД; інакше continue для
                # наступного XREAD.
                async with SessionLocal() as db:
                    turn = await turn_service.get_by_id(db, turn_id)
                if turn is None or turn.status in TURN_TERMINAL_STATUSES:
                    return
                continue
            for _stream, entries in response:
                for entry_id, fields in entries:
                    eid = entry_id.decode("ascii")
                    event = chat_pb2.ChatEvent()
                    event.ParseFromString(fields[b"pb"])
                    event.event_id = eid
                    yield event
                    cursor = eid
                    if event.WhichOneof("kind") in ("done", "error"):
                        return

    async def cleanup(self, turn_id: int) -> None:
        """No-op: terminal event лишається у stream до TURN_STREAM_TTL_S
        (24h), щоб late-reconnect міг replay-нути final state без БД-fallback-у."""
        return

    async def delete(self, turn_id: int) -> None:
        await binary_cache.delete(self._key(turn_id))
