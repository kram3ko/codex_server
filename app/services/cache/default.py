"""Process-wide async Redis client singletons."""

from redis.asyncio import Redis

from app.services.cache.client import build_cache

cache: Redis = build_cache()
binary_cache: Redis = build_cache(decode_responses=False)
