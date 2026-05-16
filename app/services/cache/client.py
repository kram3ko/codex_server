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


def build_cache() -> Redis:
    log.info("redis_client_built", host=urlparse(settings.REDIS_URL).hostname, mode="text")
    return from_url(
        settings.REDIS_URL,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        health_check_interval=30,
    )


def build_binary_cache() -> Redis:
    log.info("redis_client_built", host=urlparse(settings.REDIS_URL).hostname, mode="binary")
    return from_url(
        settings.REDIS_URL,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=False,
        health_check_interval=30,
    )
