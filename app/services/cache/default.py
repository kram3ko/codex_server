"""Process-wide async Redis client singleton."""

from redis.asyncio import Redis

from app.services.cache.client import build_cache

cache: Redis = build_cache()
