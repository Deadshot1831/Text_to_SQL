"""Shared Redis client factory (optional).

Returns a connected client when REDIS_URL is set, else None so callers fall back
to their in-memory store. Cached so we reuse one connection pool per process.
"""
from __future__ import annotations

from functools import lru_cache


@lru_cache
def get_redis():
    from app.config import get_settings

    url = get_settings().redis_url
    if not url:
        return None
    import redis

    return redis.Redis.from_url(url, decode_responses=True)
