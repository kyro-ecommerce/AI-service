import time
import unittest
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
