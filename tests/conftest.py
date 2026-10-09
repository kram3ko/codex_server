"""Shared fixtures. Integration tests need real Redis/Postgres and are skipped
when `TEST_REDIS_URL` / `TEST_DATABASE_URL` are not set."""

import os
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL")
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    if TEST_REDIS_URL is None:
        pytest.skip("TEST_REDIS_URL not set")
    client = from_url(TEST_REDIS_URL, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL not set")
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        yield engine
    finally:
        await engine.dispose()
