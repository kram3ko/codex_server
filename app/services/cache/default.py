"""Process-wide async Redis client singletons."""

from redis.asyncio import Redis

from app.services.cache.client import build_binary_cache, build_cache

cache: Redis = build_cache()
binary_cache: Redis = build_binary_cache()
