"""Caching layer for fetched market data.

Tries Redis first (shared across processes); falls back transparently to an
in-memory TTL cache if Redis is unavailable (e.g. local dev without a Redis
server running). This lets the rest of the app depend on `CacheManager`
without caring which backend is active.
"""
from __future__ import annotations

import json
import time
from typing import Any

from src.config import settings
from src.logger import get_logger

logger = get_logger(__name__)

# Refresh intervals per timeframe, per the plan (5-min for 1h, 1h for daily).
TTL_BY_TIMEFRAME = {
    "1m": 30,
    "5m": 60,
    "15m": 300,
    "1h": settings.data_sources.refresh_interval_1h,
    "4h": settings.data_sources.refresh_interval_4h,
    "1d": settings.data_sources.refresh_interval_1d,
}
DEFAULT_TTL = 300


class _InMemoryCache:
    """Simple TTL cache used when Redis is not reachable."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._store[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class CacheManager:
    """Get/set JSON-serializable values with automatic Redis→memory fallback."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._backend: Any = None
        self._is_redis = False
        try:
            import redis  # type: ignore

            client = redis.from_url(redis_url or settings.redis_url)
            client.ping()
            self._backend = client
            self._is_redis = True
            logger.info("CacheManager using Redis backend")
        except Exception as exc:  # noqa: BLE001 - any failure -> fallback
            logger.warning("Redis unavailable (%s); using in-memory cache", exc)
            self._backend = _InMemoryCache()
            self._is_redis = False

    @staticmethod
    def _ttl_for(timeframe: str) -> int:
        return TTL_BY_TIMEFRAME.get(timeframe, DEFAULT_TTL)

    def make_key(self, *parts: str) -> str:
        return ":".join(["tbot", *parts])

    def get(self, key: str) -> Any | None:
        raw = self._backend.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def set(self, key: str, value: Any, timeframe: str = "1h") -> None:
        ttl = self._ttl_for(timeframe)
        payload = json.dumps(value, default=str)
        if self._is_redis:
            self._backend.setex(key, ttl, payload)
        else:
            self._backend.set(key, payload, ttl)

    def delete(self, key: str) -> None:
        self._backend.delete(key)


# Module-level singleton for convenience.
cache = CacheManager()
