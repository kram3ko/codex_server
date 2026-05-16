"""Redis Stream-based event log per chat — durable buffer для resume-on-reconnect.

Each `ChatEvent` (proto bytes) write'иться у `chat:{chat_id}:events` зі
sliding cap (MAXLEN ~ N events) + TTL 1 година. Client'и читають через
`TailTurnRequest.after_id` (`XREAD ... BLOCK`).

Key cleanup: `XADD ~ MAXLEN 500` тримає тільки останні 500 events;
`EXPIRE` на 1 годину — захист від накопичення мертвих ключів.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.grpc_generated.codex.v1 import chat_pb2
from app.services.cache.default import cache
from app.services.codex import turn_registry

_EVENTS_KEY = "chat:{}:events"
# Long Codex turn з багатьма tools може дати кілька сотень events; 2000 з
# запасом покриває realistic worst-case (~4MB Redis per active turn).
_MAX_EVENTS = 2000
_EVENTS_TTL_S = 3600
# Короткий BLOCK щоб turn_registry checked frequently — інакше при finished+
# expired stream тейл повисне назавжди у XREAD.
_TAIL_BLOCK_MS = 2000


def _key(chat_id: int) -> str:
    return _EVENTS_KEY.format(chat_id)


def _decode(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


async def publish(chat_id: int, event: chat_pb2.ChatEvent) -> str:
    """XADD event у stream, повертає assigned event_id."""
    payload: Any = {"pb": event.SerializeToString()}
    raw_id: Any = await cache.xadd(_key(chat_id), payload, maxlen=_MAX_EVENTS, approximate=True)
    await cache.expire(_key(chat_id), _EVENTS_TTL_S)
    return _decode(raw_id)


async def tail(chat_id: int, after_id: str) -> AsyncIterator[chat_pb2.ChatEvent]:
    """Async iterator: yield ChatEvent з stream'у починаючи з `after_id`
    (порожній → "0"). BLOCK на нові events; виходить коли done/error,
    `turn_registry` показує що турн завершився, або client cancel'ить.
    Reg-check на empty XREAD — захист від нескінченного wait після cleanup."""
    cursor = after_id or "0"
    while True:
        raw: Any = cache.xread({_key(chat_id): cursor}, block=_TAIL_BLOCK_MS, count=64)
        if asyncio.iscoroutine(raw):
            raw = await raw
        if not raw:
            if await turn_registry.get(chat_id) is None:
                return
            continue
        response: list[Any] = raw
        for _stream, entries in response:
            for entry_id, fields in entries:
                eid = _decode(entry_id)
                pb_bytes = fields.get(b"pb") or fields.get("pb") or b""
                event = chat_pb2.ChatEvent()
                event.ParseFromString(pb_bytes)
                event.event_id = eid
                yield event
                cursor = eid
                if event.WhichOneof("kind") in ("done", "error"):
                    return


async def cleanup(chat_id: int) -> None:
    """Stream живе ще 60s після завершення турну на випадок останнього reconnect."""
    await cache.expire(_key(chat_id), 60)
