"""Redis pub/sub fan-out for ChatEvents.

One turn — one publisher (TurnRunner / WS handler) — many consumers
(Telegram renderer, web WS, monitoring). Channel scheme: `chat:{chat_id}:events`.

The bus does not persist events — it's a live broadcast. Persistence stays in
SQL (`messages`, `events` tables); subscribers that connect mid-turn get only
the tail. Subscribers that need replay must read the DB first, then attach.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from redis.asyncio import Redis

from app.services.codex.events import ChatEvent, event_to_frame, frame_to_event

log = structlog.get_logger(__name__)


class EventBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def channel_for(chat_id: int) -> str:
        return f"chat:{chat_id}:events"

    async def publish(self, chat_id: int, event: ChatEvent) -> None:
        frame = event_to_frame(event)
        await self._redis.publish(self.channel_for(chat_id), json.dumps(frame))

    @asynccontextmanager
    async def subscribe(self, chat_id: int) -> AsyncIterator[AsyncIterator[ChatEvent]]:
        """Async-context that yields an iterator of decoded ChatEvents.

        Caller exits the `with` to unsubscribe and release the pubsub connection.
        """
        channel = self.channel_for(chat_id)
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            yield self._iter(pubsub)
        finally:
            with _suppress_redis_errors():
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()

    @staticmethod
    async def _iter(pubsub) -> AsyncIterator[ChatEvent]:  # type: ignore[no-untyped-def]
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                yield frame_to_event(json.loads(message["data"]))
            except (ValueError, json.JSONDecodeError) as exc:
                log.warning("bus_decode_failed", error=str(exc))


@asynccontextmanager
async def _suppress_redis_errors():
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        log.warning("bus_unsubscribe_failed", error=str(exc))
