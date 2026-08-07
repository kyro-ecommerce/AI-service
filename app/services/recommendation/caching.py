"""Redis Distributed & In-Memory Fallback Caching Service for Recommendation System.

Caches recommendation results in Redis distributed cache to achieve < 5ms API response latency across multiple worker instances.
"""

import json
import time
import logging
from typing import Any, Callable

import redis
from app.core.config import REDIS_URL
from app.schemas.recommendation import RecommendationResponse

logger = logging.getLogger("ai-service.recommendation.cache")


class RecommendationCache:
    """Thread-safe Redis Distributed Cache with Local In-Memory Fallback."""

    def __init__(self, ttl_seconds: int = 300, max_size: int = 500) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._memory_store: dict[str, tuple[float, Any]] = {}
        self.redis_client: redis.Redis | None = None
        self._init_redis()

    def _init_redis(self) -> None:
        try:
            client = redis.Redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=1.5,
            )
            client.ping()
            self.redis_client = client
            logger.info("Connected to Redis Distributed Cache at %s", REDIS_URL)
        except Exception as exc:
            logger.warning("Redis connection unavailable (%s). Falling back to Local Memory Cache.", exc)
            self.redis_client = None

    @property
    def _store(self) -> dict[str, tuple[float, Any]]:
        return self._memory_store

    def get(self, key: str) -> Any | None:
        """Retrieve recommendation item from Redis or local memory if Redis is unavailable."""
        if self.redis_client is not None:
            try:
                cached_json = self.redis_client.get(key)
                if cached_json:
                    logger.debug("Redis Cache HIT for key: %s", key)
                    try:
                        return RecommendationResponse.model_validate_json(cached_json)
                    except Exception:
                        return json.loads(cached_json)
            except Exception as exc:
                logger.warning("Redis get error (%s). Falling back to local memory.", exc)

        if key not in self._memory_store:
            return None

        timestamp, val = self._memory_store[key]
        if (time.time() - timestamp) > self.ttl_seconds:
            del self._memory_store[key]
            return None

        logger.debug("Local Memory Cache HIT for key: %s", key)
        return val

    def set(self, key: str, val: Any) -> None:
        """Set recommendation item in Redis and local memory store."""
        if self.redis_client is not None:
            try:
                if isinstance(val, RecommendationResponse):
                    json_data = val.model_dump_json()
                else:
                    json_data = json.dumps(val)
                px_ms = max(1, int(self.ttl_seconds * 1000))
                self.redis_client.set(name=key, value=json_data, px=px_ms)
            except Exception as exc:
                logger.warning("Redis set error (%s). Storing in local memory.", exc)


        if len(self._memory_store) >= self.max_size:
            oldest_key = min(self._memory_store.keys(), key=lambda k: self._memory_store[k][0])
            del self._memory_store[oldest_key]

        self._memory_store[key] = (time.time(), val)


    def clear(self) -> None:
        """Clear all cached entries."""
        if self.redis_client is not None:
            try:
                self.redis_client.flushdb()
            except Exception as exc:
                logger.warning("Failed to flush Redis: %s", exc)
        self._memory_store.clear()

    def __len__(self) -> int:
        return len(self._memory_store)



# Global Cache Instances
recommendation_response_cache = RecommendationCache(ttl_seconds=300, max_size=500)


def get_cached_recommendation(
    cache_key: str,
    compute_fn: Callable[[], RecommendationResponse | None],
) -> RecommendationResponse | None:
    """Helper wrapper to fetch from Redis/Memory cache or compute and cache result."""
    cached = recommendation_response_cache.get(cache_key)
    if cached is not None:
        return cached

    result = compute_fn()
    if result is not None:
        recommendation_response_cache.set(cache_key, result)

    return result

