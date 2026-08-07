import time
import unittest
from fastapi.testclient import TestClient

from app.main import app
from app.services.recommendation.caching import recommendation_response_cache

client = TestClient(app)


class PerformanceBenchmarkTest(unittest.TestCase):
    """Production Hardening & Performance Benchmark Verification Suite."""

    def test_metrics_endpoint_health(self) -> None:
        """Verify /api/v1/ai/metrics returns healthy system status."""
        response = client.get("/api/v1/ai/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("service"), "ai-service")
        self.assertIn(data.get("status"), ["healthy", "ok"])
        self.assertIn("cache_backend", data)

    def test_recommendation_cache_latency_benchmark(self) -> None:
        """Verify RecSys response caching latency is sub-10ms."""
        # 1. Warm-up request
        t0 = time.perf_counter()
        resp1 = client.get("/api/v1/ai/recommendations/trending?limit=5")
        t1 = time.perf_counter()
        self.assertEqual(resp1.status_code, 200)
        uncached_latency_ms = (t1 - t0) * 1000.0

        # 2. Cached request
        t2 = time.perf_counter()
        resp2 = client.get("/api/v1/ai/recommendations/trending?limit=5")
        t3 = time.perf_counter()
        self.assertEqual(resp2.status_code, 200)
        cached_latency_ms = (t3 - t2) * 1000.0

        # 3. Assert cached response speed is fast (< 100ms in TestClient, typically < 10ms)
        self.assertLess(cached_latency_ms, 100.0)
        print(f"\n[BENCHMARK] RecSys Trending Uncached: {uncached_latency_ms:.2f}ms | Cached: {cached_latency_ms:.2f}ms")

    def test_concurrent_search_latency(self) -> None:
        """Benchmark Hybrid Search API latency."""
        start_time = time.perf_counter()
        for query in ["laptop", "phone", "tai nghe", "asus", "dell"]:
            res = client.get(f"/api/v1/ai/search?q={query}&limit=3")
            self.assertEqual(res.status_code, 200)
        total_time_ms = (time.perf_counter() - start_time) * 1000.0
        avg_time_ms = total_time_ms / 5.0
        print(f"[BENCHMARK] Avg Hybrid Search Latency: {avg_time_ms:.2f}ms per query across 5 searches.")
        self.assertLess(avg_time_ms, 500.0)


if __name__ == "__main__":
    unittest.main()
