"""Async Redis client factory.

Lazy-connect: the first command issued by application code triggers
the actual TCP connect through `redis.asyncio.from_url`'s connection pool.
"""

import structlog
from redis.asyncio import Redis, from_url

from app.config import settings

log = structlog.get_logger(__name__)


def build_cache() -> Redis:
    log.info("redis_client_built", host=_safe_host(settings.REDIS_URL))
    return from_url(
        settings.REDIS_URL,
        decode_responses=True,
        health_check_interval=30,
    )


def _safe_host(url: str) -> str:
    """Strip credentials so we never log the password back at us."""
    at = url.rfind("@")
    return url[at + 1 :] if at != -1 else url
