import time
import unittest
from unittest.mock import MagicMock
from app.schemas.product import Product
from app.services.recommendation.caching import RecommendationCache, get_cached_recommendation
from app.services.recommendation_service import recommend_similar_products


class CachingUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.laptop1 = Product(
            product_id=1,
            title="Lenovo ThinkPad X1 Carbon",
            category_name="laptop",
            brand="Lenovo",
            is_active=True,
        )
        self.laptop2 = Product(
            product_id=2,
            title="Lenovo Legion 5",
            category_name="laptop",
            brand="Lenovo",
            is_active=True,
        )
        self.products = [self.laptop1, self.laptop2]

    def test_cache_hit_and_eviction(self) -> None:
        """Verify cache hit and eviction policy."""
        cache = RecommendationCache(ttl_seconds=1, max_size=2)
        cache.set("k1", "v1")
        cache.set("k2", "v2")

        self.assertEqual(cache.get("k1"), "v1")

        # Evict oldest by adding third item
        cache.set("k3", "v3")
        self.assertEqual(len(cache._store), 2)

    def test_cache_ttl_expiration(self) -> None:
        """Verify TTL expiration."""
        cache = RecommendationCache(ttl_seconds=0.1, max_size=10)
        cache.set("key1", "val1")
        self.assertEqual(cache.get("key1"), "val1")

        time.sleep(0.15)
        self.assertIsNone(cache.get("key1"))

    def test_redis_keys_are_namespaced(self) -> None:
        cache = RecommendationCache(ttl_seconds=60, key_prefix="ai:rec:")
        redis_client = MagicMock()
        cache.redis_client = redis_client

        cache.set("user:42", {"product_ids": [1, 2]})

        redis_client.set.assert_called_once()
        self.assertEqual(redis_client.set.call_args.kwargs["name"], "ai:rec:user:42")

    def test_clear_deletes_only_owned_redis_namespace(self) -> None:
        cache = RecommendationCache(ttl_seconds=60, key_prefix="ai:rec:")
        redis_client = MagicMock()
        redis_client.scan_iter.return_value = iter(["ai:rec:user:1", "ai:rec:product:2"])
        cache.redis_client = redis_client
        cache._memory_store["local"] = (time.time(), "value")

        cache.clear()

        redis_client.scan_iter.assert_called_once_with(match="ai:rec:*")
        redis_client.delete.assert_called_once_with("ai:rec:user:1", "ai:rec:product:2")
        redis_client.flushdb.assert_not_called()
        self.assertEqual(cache._memory_store, {})

    def test_recommendation_service_cache_latency(self) -> None:
        """Verify that second call to recommend_similar_products is served from cache instantaneously."""
        # First call (cold compute)
        t0 = time.time()
        res1 = recommend_similar_products(self.products, target_product_id=1, limit=2)
        duration_cold = time.time() - t0

        # Second call (cache hit)
        t1 = time.time()
        res2 = recommend_similar_products(self.products, target_product_id=1, limit=2)
        duration_cached = time.time() - t1

        self.assertIsNotNone(res1)
        self.assertIsNotNone(res2)
        self.assertEqual(res1.recommendations[0].product_id, res2.recommendations[0].product_id)
        self.assertLessEqual(duration_cached, duration_cold + 0.05)


if __name__ == "__main__":
    unittest.main()
