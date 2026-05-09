"""Async Redis client factory."""

from urllib.parse import urlparse

import structlog
from redis.asyncio import Redis, from_url

from app.config import settings

log = structlog.get_logger(__name__)


def build_cache() -> Redis:
    log.info("redis_client_built", host=urlparse(settings.REDIS_URL).hostname)
    return from_url(
        settings.REDIS_URL,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        health_check_interval=30,
    )
