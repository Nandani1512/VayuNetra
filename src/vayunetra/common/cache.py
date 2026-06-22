from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from vayunetra.common.config import get_settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def cache_get_json(key: str) -> Any | None:
    raw = await get_redis().get(key)
    return json.loads(raw) if raw else None


async def cache_set_json(key: str, value: Any, ttl_s: int) -> None:
    await get_redis().set(key, json.dumps(value, default=str), ex=ttl_s)
