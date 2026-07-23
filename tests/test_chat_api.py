import unittest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class ChatAPITest(unittest.TestCase):
    def test_chat_api_success_with_fallback(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "Tư vấn cho mình laptop 16GB RAM học lập trình", "limit": 3},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertIn("recommended_products", data)
        self.assertIsInstance(data["reply"], str)
        self.assertGreater(len(data["reply"]), 10)
        self.assertIsInstance(data["recommended_products"], list)

        if len(data["recommended_products"]) > 0:
            prod = data["recommended_products"][0]
            self.assertIn("product_id", prod)
            self.assertIn("title", prod)

    def test_chat_api_mouse_consultation(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "Cho mình hỏi chuột không dây dùng văn phòng loại nào tốt?", "limit": 2},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertLessEqual(len(data["recommended_products"]), 2)

    def test_chat_api_invalid_limit(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "Cần mua tai nghe", "limit": 0},
        )
        # Should return 422 Unprocessable Entity due to Pydantic validation (ge=1)
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
