import unittest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class SearchAPITest(unittest.TestCase):
    def test_search_api_success(self) -> None:
        response = client.post(
            "/api/v1/ai/search",
            json={"query": "laptop 16gb", "limit": 5},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("query", data)
        self.assertIn("results", data)
        self.assertEqual(data["query"], "laptop 16gb")
        self.assertIsInstance(data["results"], list)

        if len(data["results"]) > 0:
            first_result = data["results"][0]
            self.assertIn("product_id", first_result)
            self.assertIn("title", first_result)
            self.assertIn("score", first_result)
            self.assertGreater(first_result["score"], 0.0)

    def test_search_api_empty_query(self) -> None:
        response = client.post(
            "/api/v1/ai/search",
            json={"query": "   ", "limit": 5},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["results"]), 0)

    def test_search_api_intent_guardrail_mouse(self) -> None:
        response = client.post(
            "/api/v1/ai/search",
            json={"query": "chuột không dây", "limit": 5},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        if len(data["results"]) > 0:
            first = data["results"][0]
            # Must prioritize mouse products
            self.assertTrue(
                "chuột" in first["title"].lower()
                or "mouse" in str(first.get("category_name", "")).lower()
                or "logitech" in first["title"].lower()
            )

    def test_search_api_limit_parameter(self) -> None:
        response = client.post(
            "/api/v1/ai/search",
            json={"query": "tai nghe", "limit": 2},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertLessEqual(len(data["results"]), 2)


if __name__ == "__main__":
    unittest.main()
