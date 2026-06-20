import logging
from collections.abc import AsyncIterator

import redis.asyncio as redis

from inference_api.config import settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    return _redis_client


async def get_redis() -> AsyncIterator[redis.Redis]:
    yield get_redis_client()


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        finally:
            _redis_client = None
