"""Per-user pub/sub: sidebar busy-індикатор без polling."""

from typing import Any

import orjson
import structlog
from redis.exceptions import RedisError

from app.services.cache.default import cache
from app.services.chat_activity.schemas import ChatActivityKind

log = structlog.get_logger(__name__)


def channel_for_user(user_id: int) -> str:
    return f"chat_activity:{user_id}"


async def publish_turn_started(user_id: int, chat_id: int, turn_id: int) -> None:
    await _publish(user_id, chat_id=chat_id, turn_id=turn_id, kind=ChatActivityKind.STARTED)


async def publish_turn_ended(user_id: int, chat_id: int, turn_id: int) -> None:
    await _publish(user_id, chat_id=chat_id, turn_id=turn_id, kind=ChatActivityKind.ENDED)


async def _publish(
    user_id: int,
    *,
    chat_id: int,
    turn_id: int,
    kind: ChatActivityKind,
) -> None:
    """Telemetry boundary — Redis hiccup / network не повинна валити turn-lifecycle CAS."""
    payload = orjson.dumps({"chat_id": chat_id, "turn_id": turn_id, "kind": kind.value})
    try:
        await cache.publish(channel_for_user(user_id), payload)
    except (RedisError, OSError):
        log.warning(
            "chat_activity_publish_failed",
            user_id=user_id,
            chat_id=chat_id,
            kind=kind.value,
            exc_info=True,
        )


def deserialize(raw: bytes | str) -> dict[str, Any] | None:
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        log.warning("chat_activity_bad_payload")
        return None
    return data if isinstance(data, dict) else None
