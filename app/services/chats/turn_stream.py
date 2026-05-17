"""Redis Stream-based event log per chat — durable buffer для resume-on-reconnect.

Each `ChatEvent` (raw proto bytes) write'иться у `chat:{chat_id}:events` зі
sliding cap (MAXLEN ~ N events) + TTL 1 година. Client'и читають через
`TailTurnRequest.after_id` (`XREAD ... BLOCK`).

Bytes path: використовуємо `binary_cache` (`decode_responses=False`), бо
protobuf payload може містити НЕ-UTF8 байти, які ламали б text-decoder
основного `cache`-клієнта. Це канонічний redis-py pattern — окремий pool
per `decode_responses` mode.
"""

from collections.abc import AsyncIterator

from app.grpc_generated.codex.v1 import chat_pb2
from app.rpc.chat.mappers import error_event
from app.services.cache.default import binary_cache
from app.services.codex import turn_registry
from app.services.codex.error_codes import CodexErrorCode

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


async def reset(chat_id: int) -> None:
    """DEL stream key. Викликати у handler ДО `spawn()` для свіжого turn-у —
    гарантує що `tail(after_id="0")` стартує з порожнього стрім-а і catch-нe
    кожен XADD з background-у (без race з `$`-cursor проти першого publish)."""
    await binary_cache.delete(_key(chat_id))


async def publish(chat_id: int, event: chat_pb2.ChatEvent) -> str:
    """XADD event у stream, повертає assigned event_id."""
    raw_id = await binary_cache.xadd(
        _key(chat_id), {"pb": event.SerializeToString()}, maxlen=_MAX_EVENTS, approximate=True
    )
    await binary_cache.expire(_key(chat_id), _EVENTS_TTL_S)
    return raw_id.decode("ascii")


async def tail(chat_id: int, after_id: str) -> AsyncIterator[chat_pb2.ChatEvent]:
    """Yield ChatEvent з stream'у. Порожній `after_id` → `$` (лише нові,
    стрім shared across turns, leftover-events попереднього turn-у дадуть
    duplicate). Явний `after_id` → replay (TailTurn resume). Виходить на
    done/error.

    Якщо registry вже порожній і стрім пустий (background завершився, TTL
    стрім-ключа міг встигнути минути ДО reconnect) — віддаємо synthetic
    STREAM_DROPPED замість silent return, щоб UI міг детермінованно
    закрити turn placeholder."""
    cursor = after_id or "$"
    while True:
        response = await binary_cache.xread({_key(chat_id): cursor}, block=_TAIL_BLOCK_MS, count=64)
        if not response:
            if await turn_registry.get(chat_id) is None:
                yield error_event(
                    CodexErrorCode.STREAM_DROPPED,
                    "turn stream expired before terminal event",
                )
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


async def cleanup(chat_id: int) -> None:
    """Stream живе ще 60s після завершення турну на випадок останнього reconnect."""
    await binary_cache.expire(_key(chat_id), 60)
