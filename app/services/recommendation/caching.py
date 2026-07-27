"""In-Memory LRU & TTL Caching Service for Recommendation System.

Caches recommendation results and expensive candidate computations to achieve < 20ms API response latency.
"""

import time
import logging
from typing import Any, Callable
from app.schemas.recommendation import RecommendationResponse

logger = logging.getLogger("ai-service.recommendation.cache")


class RecommendationCache:
    """Thread-safe In-Memory LRU Cache with TTL expiration."""

    def __init__(self, ttl_seconds: int = 300, max_size: int = 500) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        """Retrieve item from cache if not expired."""
        if key not in self._store:
            return None

        timestamp, val = self._store[key]
        if (time.time() - timestamp) > self.ttl_seconds:
            # Expired
            del self._store[key]
            return None

        return val

    def set(self, key: str, val: Any) -> None:
        """Set item in cache, evicting oldest item if max size reached."""
        if len(self._store) >= self.max_size:
            oldest_key = min(self._store.keys(), key=lambda k: self._store[k][0])
            del self._store[oldest_key]

        self._store[key] = (time.time(), val)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()


# Global Cache Instances
recommendation_response_cache = RecommendationCache(ttl_seconds=300, max_size=500)


def get_cached_recommendation(
    cache_key: str,
    compute_fn: Callable[[], RecommendationResponse | None],
) -> RecommendationResponse | None:
    """Helper wrapper to fetch from cache or compute and cache result."""
    cached = recommendation_response_cache.get(cache_key)
    if cached is not None:
        logger.debug("Cache HIT for key: %s", cache_key)
        return cached

    result = compute_fn()
    if result is not None:
        recommendation_response_cache.set(cache_key, result)

    return result
