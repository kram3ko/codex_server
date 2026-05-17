"""Async Redis client factory.

Two clients — два окремих ConnectionPool. `decode_responses` у redis-py
живе на Connection, не на Redis wrapper: `Redis(connection_pool=shared,
decode_responses=False)` мовчки ігнорується. Тому для binary payload-ів
(protobuf на streams) потрібен власний pool з `decode_responses=False`.
"""

from urllib.parse import urlparse

import structlog
from redis.asyncio import Redis, from_url

from app.config import settings

log = structlog.get_logger(__name__)


def build_cache(*, decode_responses: bool = True) -> Redis:
    mode = "text" if decode_responses else "binary"
    log.info("redis_client_built", host=urlparse(settings.REDIS_URL).hostname, mode=mode)
    return from_url(
        settings.REDIS_URL,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=decode_responses,
        health_check_interval=30,
    )
